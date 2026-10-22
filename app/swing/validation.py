"""Read-only evaluation of frozen S3 ledger cohorts. Never backfills a strategy."""
from collections import Counter
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3

from app.swing.ledger import PaperLedger, PaperNotFound, empty_state, reduce_event
from app.swing.models import utc
from app.swing.repository import SwingRepository
from app.swing.setups import digest
from app.swing.validation_protocol import Study, code_hashes, runtime_fingerprint, schedule
from app.swing.validation_stats import metric_failures, summarize


def audit_vintage(repository, vintage):
    verified = repository.verify(vintage)
    report = repository.report(vintage)
    failures = []
    # Coverage alone is never proof of historic membership, publication or actions.
    if report.get("membership_scope") != "verified_point_in_time":
        failures.append("point_in_time_universe_unverified")
    failures.extend(["historical_publication_unverified", "historical_adjustment_identity_unverified",
                     "historical_execution_context_unverified"])
    if report.get("benchmark", {}).get("status") != "available":
        failures.append("nifty500_benchmark_unavailable")
    if not report.get("summary", {}).get("complete_trailing_price_histories"):
        failures.append("incomplete_price_histories")
    return {"vintage": vintage, "verification": verified, "coverage_sha256": digest(report),
            "summary": report.get("summary"), "benchmark": {k: report.get("benchmark", {}).get(k) for k in
                ("ticker", "status", "rows", "open_rows", "missing_session_count")},
            "membership_scope": report.get("membership_scope"), "failures": failures,
            "limitations": report.get("limitations", []), "model_status": "RESEARCH_ONLY",
            "notice": "S1 schema v1 cannot attest historical publication, membership, adjustment and execution context"}


def extract_panel(ledger, conn, account_id, variant, window, window_schedule, study, frozen, calendar, now):
    """One consistent ledger snapshot for all account state/plans/events in a report."""
    state = ledger._state(conn, account_id)  # verifies the entire chain before consuming any outcome
    events = [dict(r) for r in conn.execute("SELECT * FROM events WHERE account_id=? ORDER BY seq", (account_id,))]
    plans = {r[0]: ledger.plan(account_id, r[0], conn) for r in conn.execute("SELECT id FROM plans WHERE account_id=?", (account_id,))}
    decoded = [{**e, "payload": json.loads(e["payload"])} for e in events]
    failures = []
    if str(state["initial_cash"]) != f"{study.capital_inr:.2f}" or decoded[0]["payload"]["session"] != window_schedule["seed"]:
        failures.append("funding_or_seed_mismatch")
    if state["last_session"] != str(window.end) or state["positions"] or any(
            o["lifecycle"] in {"ARMED", "OPEN", "UNRESOLVED", "CANDIDATE"} for o in state["orders"].values()):
        failures.append("incomplete_window_or_open_liabilities")
    if state["unresolved"] or any(t["outcome_status"] != "RESOLVED" for t in state["closed"]):
        failures.append("unresolved_paths")
    captures = {e["payload"]["plan_id"]: e for e in decoded if e["kind"] == "PLAN_CAPTURED"}
    for key, plan in plans.items():
        config = dict(plan.configuration)
        original = dict(variant.configuration)
        # S3 reconciled NAV changes the sizing capital, not the frozen hypothesis.
        config.pop("SWING_CAPITAL_INR", None)
        original.pop("SWING_CAPITAL_INR", None)
        if config != original or plan.config_hash != digest(plan.configuration) or plan.implementation_hash != frozen["setup_implementation"]:
            failures.append("plan_configuration_or_implementation_mismatch")
        if plan.strategy not in study.strategies or not str(window.start) <= str(plan.session) <= window_schedule["signal_end"]:
            failures.append("strategy_or_purged_signal_boundary")
        capture_at = utc(datetime.fromisoformat(captures[key]["created_at"]))
        opening = calendar.calendar.session_open(str(plan.first_entry_session)).to_pydatetime()
        if not utc(plan.evidence.available_at) <= utc(plan.decision_at) <= capture_at < opening:
            failures.append("plan_not_captured_before_entry")
        if plan.missing_gates or not all((plan.evidence.identity_verified, plan.evidence.publication_verified,
                plan.evidence.trading_basis_verified, plan.evidence.sector_verified,
                plan.evidence.earnings_verified, plan.evidence.surveillance_verified)):
            failures.append("plan_evidence_incomplete")
    if any(utc(datetime.fromisoformat(e["created_at"])) > utc(now) for e in decoded):
        failures.append("future_ledger_events")
    instants = [utc(datetime.fromisoformat(e["created_at"])) for e in decoded]
    if instants != sorted(instants):
        failures.append("nonmonotonic_recorded_timeline")
    synthetic = "SYNTHETIC" in json.dumps([account_id, [p.ticker for p in plans.values()],
                     [p.evidence.source_revision for p in plans.values()], [p.evidence.vintage for p in plans.values()]]).upper()
    if synthetic or any(p.ticker.startswith("TEST") for p in plans.values()):
        failures.append("synthetic_evidence")
    reconstructed = empty_state(account_id)
    daily = []
    entry_exposure = 0.
    observed = {e["payload"]["session"]: e["payload"]["observations"] for e in decoded if e["kind"] == "SESSION_MARKED"}
    rejection_counts = Counter()
    for event in decoded:
        kind, payload = event["kind"], event["payload"]
        reduce_event(reconstructed, kind, payload)
        if kind == "BUY_FILLED":
            opening_values = []
            for ticker, position in reconstructed["positions"].items():
                bar = observed.get(payload["session"], {}).get(ticker, {}).get("bar")
                if not bar or bar.get("open") is None:
                    failures.append("entry_book_open_marks_unavailable")
                    continue
                opening_values.append(position["quantity"]*float(bar["open"]))
            held = sum(opening_values)
            opening_nav = float(reconstructed["cash"])+held
            if opening_nav <= 0:
                failures.append("invalid_opening_nav")
            else:
                entry_exposure = max(entry_exposure, held/opening_nav)
        if kind in {"ORDER_CANCELLED", "ORDER_EXPIRED", "ALLOCATION_REJECTED", "DATA_UNRESOLVED"}:
            rejection_counts[str(payload.get("reason", payload.get("reasons", kind)))] += 1
        if kind != "SESSION_MARKED":
            continue
        if payload["nav"] is None or payload["benchmark_nav"] is None or payload["quality"] != "OBSERVED":
            failures.append("unavailable_nav_or_benchmark")
            continue
        nav, cash, benchmark = (float(payload[k]) for k in ("nav", "cash", "benchmark_nav"))
        if nav <= 0 or cash < 0 or benchmark <= 0:
            failures.append("invalid_nav")
            continue
        daily.append({"session": payload["session"], "nav": nav, "cash": cash,
                      "benchmark_nav": benchmark, "exposure": (nav-cash)/nav})
    if [d["session"] for d in daily] != window_schedule["sessions"]:
        failures.append("missing_or_duplicate_exact_marks")
    trades = []
    for trade in state["closed"]:
        plan = plans[trade["plan_id"]]
        if not str(window.start) <= trade["entry_session"] <= trade["exit_session"] <= str(window.end):
            failures.append("outcome_crosses_window_boundary")
        risk = float(trade["initial_risk"])
        if risk <= 0:
            failures.append("invalid_initial_risk")
            continue
        buys = [e["payload"] for e in decoded if e["kind"] == "BUY_FILLED" and e["payload"]["plan_id"] == plan.id]
        sell_gross = sum(float(e["payload"]["gross_value"]) for e in decoded if e["kind"] == "SELL_FILLED" and e["payload"]["plan_id"] == plan.id)
        trades.append({"plan_id": plan.id, "signal_session": str(plan.session), "entry_session": trade["entry_session"],
            "exit_session": trade["exit_session"], "pnl": float(trade["net_pnl"]), "r": float(trade["net_pnl"])/risk,
            "risk": risk, "sector": trade["sector"], "buy_gross": sum(float(b["gross_value"]) for b in buys),
            "sell_gross": sell_gross, "participation": sum(b["quantity"] for b in buys)/plan.features.average_volume,
            "holding_sessions": len(calendar.calendar.sessions_in_range(trade["entry_session"], trade["exit_session"]))})
    cfg = variant.configuration
    from app.core.config import Settings
    from app.swing.setups import costs
    rates = costs(Settings(_env_file=None, **cfg))
    head = events[-1]
    return {"account_id": account_id, "window": window.id, "capital": study.capital_inr,
            "_events": decoded, "_plans": plans,
            "entry_exposure": entry_exposure,
            "sessions": window_schedule["sessions"], "daily": daily, "trades": trades,
            "attempts": len(plans), "filled": len({e["payload"]["plan_id"] for e in decoded if e["kind"] == "BUY_FILLED"}),
            "buy_multiplier": rates.buy_multiplier, "sell_multiplier": rates.sell_multiplier,
            "rejections": dict(rejection_counts), "failures": sorted(set(failures)),
            "vintages": sorted({p.evidence.vintage for p in plans.values()} | {e["payload"]["vintage"] for e in decoded if e["kind"] == "SESSION_MARKED"}),
            "manifest": {"account_id": account_id, "version": state["version"], "event_head": head["event_hash"],
                         "plans": sorted(plans), "synthetic": synthetic},
            "coverage_scope": "Captured opportunities only; data-filtered or uncaptured universe members are not fill attempts"}


class ValidationService:
    def __init__(self, registry, config, clock, calendar):
        self.registry, self.config, self.clock, self.calendar = registry, config, clock, calendar
        self.ledger = PaperLedger(Path(config.SWING_PAPER_DB_PATH), clock, config.SQLITE_BUSY_TIMEOUT_SEC)
        self.repository = SwingRepository(Path(config.SWING_DB_PATH), config.SQLITE_BUSY_TIMEOUT_SEC, calendar)
        protected = [Path(config.SWING_DB_PATH), Path(config.SWING_PAPER_DB_PATH), Path("data/history.db")]
        if config.SWING_SOURCE_DB_PATH:
            protected.append(Path(config.SWING_SOURCE_DB_PATH))
        if registry.path.resolve() in {p.resolve() for p in protected} or any(
                p.exists() and registry.path.exists() and p.samefile(registry.path) for p in protected):
            raise ValueError("Validation registry must be separate from source, observations and trading histories")

    def evaluate(self, study_id, *, holdout=False):
        registration = self.registry.study(study_id)
        frozen = registration["payload"]
        study = Study.model_validate(frozen["protocol"])
        if code_hashes() != frozen["code_hashes"] or runtime_fingerprint() != frozen["runtime"] or schedule(study, self.calendar) != frozen["schedule"]:
            raise ValueError("Code/calendar changed after registration; this study cannot be retuned")
        if holdout:
            existing = self.registry.holdout_report(study_id)
            if existing:
                return existing
            self.registry.claim_holdout(study_id, self.calendar)  # durable before any outcome read
        selected = study.windows(holdout=holdout)
        panels, failures, inputs, vintages = {}, [], [], set()
        try:
            with self.ledger.connection() as conn:
                for variant in study.variants:
                    for phase, window in selected:
                        key = variant.name+":"+window.id
                        try:
                            if self.calendar.calendar.session_close(str(window.end)).to_pydatetime() >= utc(self.clock.now()):
                                raise ValueError("window_not_completed")
                            panel = extract_panel(self.ledger, conn, variant.accounts[window.id], variant, window,
                                frozen["schedule"]["windows"][window.id], study, frozen, self.calendar, self.clock.now())
                            panels[key] = panel
                            inputs.append(panel["manifest"])
                            vintages.update(panel["vintages"])
                            failures.extend(key+":"+f for f in panel["failures"])
                        except (ValueError, KeyError, TypeError, sqlite3.DatabaseError) as exc:
                            failures.append(key+":"+type(exc).__name__+":"+str(exc))
        except (FileNotFoundError, ValueError, sqlite3.DatabaseError) as exc:
            failures.append("ledger_unavailable:"+type(exc).__name__)
        audits = []
        for vintage in sorted(vintages):
            try:
                audit = audit_vintage(self.repository, vintage)
                audits.append(audit)
                failures.extend(vintage+":"+f for f in audit["failures"])
            except (ValueError, FileNotFoundError, sqlite3.DatabaseError):
                failures.append(vintage+":source_evidence_unavailable")
        results, aggregates = {}, {}
        for variant in study.variants:
            for phase, window in selected:
                key = variant.name+":"+window.id
                panel = panels.get(key)
                results[key] = {"phase": phase, "role": variant.role, "failures": panel["failures"] if panel else ["unavailable"],
                    "rejections": panel["rejections"] if panel else {},
                    "observed_counts": {"closed_trades": len(panel["trades"]), "nav_sessions": len(panel["daily"]),
                                        "expected_sessions": len(panel["sessions"]), "captured_opportunities": panel["attempts"]} if panel else None,
                    "metrics": summarize([panel], study.gates, frozen["schedule"]["block_sessions"]) if panel and not panel["failures"] else None}
            valid = [panels[variant.name+":"+w.id] for phase, w in selected if phase in {"test", "holdout"}
                     and variant.name+":"+w.id in panels and not panels[variant.name+":"+w.id]["failures"]]
            expected = sum(phase in {"test", "holdout"} for phase, _ in selected)
            aggregates[variant.name] = summarize(valid, study.gates, frozen["schedule"]["block_sessions"]) if len(valid) == expected else None
        primary = next(v for v in study.variants if v.role == "primary")
        primary_metrics = aggregates[primary.name]
        failures.extend(metric_failures(primary_metrics, study.gates) if primary_metrics else ["primary_cohort_incomplete"])
        neighbors = [v for v in study.variants if v.role == "parameter"]
        if len(neighbors) < study.gates.min_neighbors:
            failures.append("missing_parameter_neighbors")
        for v in neighbors:
            m = aggregates[v.name]
            if not m or not primary_metrics or m["mean_net_r"] is None or primary_metrics["mean_net_r"] is None or m["mean_net_r"] < study.gates.neighbor_ratio*primary_metrics["mean_net_r"] or metric_failures(m, study.gates):
                failures.append(v.name+":unstable_parameter_neighbor")
        from app.core.config import Settings
        from app.swing.execution import ArchiveReplayMarket
        from app.swing.validation_stress import stress_replay
        execution_stresses = {}
        for scenario in ("delay", "gap", "halt"):
            derived, details, errors = [], [], []
            for phase, window in selected:
                if phase not in {"test", "holdout"}:
                    continue
                source = panels.get(primary.name+":"+window.id)
                if source is None or source["failures"]:
                    errors.append(window.id+":primary_window_unavailable_or_invalid")
                    continue
                try:
                    cfg = Settings(_env_file=None, **{**self.config.model_dump(), **primary.configuration})
                    stress = stress_replay(source, scenario, study.gates, cfg, self.calendar,
                        ArchiveReplayMarket(self.repository, self.calendar), primary, window,
                        frozen["schedule"]["windows"][window.id], study, frozen, self.clock.now())
                    derived.append(stress.pop("panel"))
                    details.append(stress)
                    errors.extend(window.id+":"+f for f in derived[-1]["failures"])
                except (ValueError, KeyError, TypeError, OSError, sqlite3.DatabaseError) as exc:
                    errors.append(window.id+":"+type(exc).__name__+":"+str(exc))
            metrics = summarize(derived, study.gates, frozen["schedule"]["block_sessions"]) if derived and not errors else None
            errors.extend(metric_failures(metrics, study.gates) if metrics else ["execution_stress_incomplete"])
            execution_stresses[scenario] = {"metrics": metrics, "experiments": details, "failures": sorted(set(errors))}
            failures.extend(scenario+":"+e for e in errors)
        if not registration["signature"] or not self.registry.key:
            failures.append("unsigned_protocol_or_report")
        if not holdout:
            failures.append("terminal_holdout_not_opened")
        # A terminal result cannot substitute for independently passing OOS folds.
        if holdout:
            with self.registry.connection() as conn:
                previous = [json.loads(r[0]) for r in conn.execute("SELECT record FROM reports WHERE study_id=? AND phase='development'", (study_id,))]
            if not any(self.registry.verify(r).get("development_gates_passed") for r in previous):
                failures.append("out_of_sample_development_gates_not_passed")
        failures = sorted(set(failures))
        payload = {"study_id": study_id, "phase": "holdout" if holdout else "development",
            "protocol_sha256": registration["sha256"], "code_hashes": frozen["code_hashes"],
            "runtime": frozen["runtime"],
            "calendar_sha256": digest(frozen["schedule"]), "input_manifest": inputs,
            "input_sha256": digest(inputs), "source_audits": audits, "windows": results,
            "out_of_sample": aggregates, "execution_stresses": execution_stresses, "failures": failures,
            "development_gates_passed": not [f for f in failures if f != "terminal_holdout_not_opened"],
            "model_status": "RESEARCH_ONLY" if failures else "VALIDATED_FOR_PAPER",
            "paper_eligible": False, "scope": "Validation recommendation only; no S2/S3 eligibility or account mutation",
            "limitations": ["No evidence for evaluations performed outside this local registry",
                "S1 v1 does not establish PIT membership or historical execution context",
                "Counterfactual stresses require verified observations for every changed holding path; no missing fills imputed",
                "Block bootstrap is a dependence-aware approximation, not a guarantee of future returns",
                "All registered variants disclosed; no winner selection or multiple-model search inference",
                "HMAC authenticates a shared local key, not an independent auditor"]}
        return self.registry.save(study_id, payload["phase"], payload)

"""Prospective daily observations reconciled to immutable S3 paper events."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import re

from pydantic import TypeAdapter

from app.providers.swing_archive import file_hash, last_completed
from app.swing.forward_store import ForwardJournal, ForwardSettings
from app.swing.ledger import PaperConflict, encoded, money, reduce_event
from app.swing.models import utc
from app.swing.paper_service import PaperService
from app.swing.plans import Plan
from app.swing.service import SwingResearchService
from app.swing.setups import configuration, digest
from app.swing.validation import audit_vintage
from app.swing.validation_protocol import code_hashes, runtime_fingerprint


def fingerprint():
    root = Path(__file__).parents[2]
    return {**code_hashes(), **{p: file_hash(root/p) for p in
        ("docs/SWING_S6_CONTRACT.md", "scripts/swing_forward.py")}}


def normalized(config):
    return {k: v for k, v in dict(config).items() if k != "SWING_CAPITAL_INR"}


def synthetic(value):
    return "SYNTHETIC" in encoded(value).upper()


def observe_problem(obs, ticker, session, benchmark=False):
    bar = obs.get("bar")
    if not bar:
        return obs.get("reason") or "missing_observation"
    if bar["ticker"] != ticker or bar["session"] != session or bar["price_basis"] != "raw":
        return "observation_identity_session_or_basis"
    if bar["instrument"] != ("index" if benchmark else "equity") or bar.get("open") is None:
        return "observation_instrument_or_open"
    if not benchmark and (not bar.get("isin") or bar.get("series") not in {"EQ", "BE"}):
        return "security_identity_or_series_unverified"
    if not obs["actions_verified"] or obs["unsupported_action"]:
        return "corporate_action_context_unverified"
    return None


def reconcile(registration, previous_state, plans, events, observations, session, now, calendar):
    """Independently check observed references/cash; never generate or repair fills."""
    failures, comparisons = [], []
    state = deepcopy(previous_state)
    state["cash"], state["initial_cash"] = money(state["cash"]), money(state["initial_cash"])
    before_orders = deepcopy(state["orders"])
    instants = []
    for event in events:
        p, kind = event["payload"], event["kind"]
        at = utc(datetime.fromisoformat(event["created_at"]))
        instants.append(at)
        if at > now or at < utc(datetime.fromisoformat(registration["registered_at"])):
            failures.append("event_outside_recorded_timeline")
        if p.get("session") != session and kind != "REPLAY_RECORDED":
            failures.append("late_or_backfilled_paper_events")
        if kind == "PLAN_CAPTURED":
            plan = TypeAdapter(Plan).validate_python(plans[p["plan_id"]])
            opening = calendar.calendar.session_open(str(plan.first_entry_session)).to_pydatetime()
            if not (str(plan.session) == session and utc(plan.evidence.available_at) <= utc(plan.decision_at) <= at <= now < opening):
                failures.append("plan_not_captured_prospectively")
            if session > registration["signal_end"]:
                failures.append("new_plan_during_declared_wind_down")
            if normalized(plan.configuration) != registration["configuration"] or plan.implementation_hash != registration["setup_implementation"]:
                failures.append("plan_configuration_drift")
            if plan.missing_gates or not all((plan.evidence.identity_verified, plan.evidence.publication_verified,
                    plan.evidence.trading_basis_verified, plan.evidence.sector_verified,
                    plan.evidence.earnings_verified, plan.evidence.surveillance_verified)):
                failures.append("plan_evidence_incomplete")
        if kind in {"BUY_FILLED", "SELL_FILLED"}:
            plan = plans.get(p["plan_id"])
            obs = observations.get(p["ticker"], {})
            problem = observe_problem(obs, p["ticker"], session)
            if problem:
                failures.append("fill_unresolved:"+problem)
            if not plan:
                failures.append("fill_without_captured_plan")
                continue
            # All plan IDs are present, but only earlier sealed orders are forecasts.
            if kind == "BUY_FILLED" and p["plan_id"] not in before_orders:
                failures.append("fill_without_prior_sealed_forecast")
            bar = obs.get("bar")
            if bar and not problem:
                side = "buy_capacity" if kind == "BUY_FILLED" else "sell_capacity"
                if obs.get("tradable") is not True or obs.get(side) is None:
                    failures.append("fill_capacity_or_tradability_unverified")
                elif p["quantity"] > obs[side]:
                    failures.append("fill_exceeds_observed_capacity")
                if kind == "BUY_FILLED":
                    expected = bar["open"]
                    order = before_orders.get(p["plan_id"], {})
                    if order.get("lifecycle") != "ARMED" or p["quantity"] > order.get("quantity", 0):
                        failures.append("fill_exceeds_prior_reservation")
                    if not (plan["first_entry_session"] <= session <= plan["expiry_session"] and plan["entry_min"] <= expected <= plan["entry_max"]):
                        failures.append("fill_outside_frozen_entry_window")
                    if bar.get("isin") != plan["isin"] or obs.get("split_ratio"):
                        failures.append("entry_identity_or_action_requires_review")
                else:
                    position = state["positions"].get(p["ticker"])
                    expected = None
                    if position and bar.get("isin") == position["isin"]:
                        reason = p["reason"]
                        if reason == "STOP_GAP" and bar["open"] <= position["stop"]:
                            expected = bar["open"]
                        elif reason == "TARGET_GAP" and bar["open"] >= position["target"]:
                            expected = position["target"]
                        elif reason == "STOP" and bar["low"] <= position["stop"]:
                            expected = position["stop"]
                        elif reason == "TARGET" and bar["high"] >= position["target"] and bar["low"] > position["stop"]:
                            expected = position["target"]
                        # A previously queued partial/blocked exit uses the next open.
                        prior_position = previous_state["positions"].get(p["ticker"])
                        if prior_position and prior_position.get("exit_pending") == reason:
                            expected = bar["open"]
                    if expected is None:
                        failures.append("exit_trigger_or_identity_mismatch")
                if expected is not None and Decimal(str(p["raw_price"])) != Decimal(str(expected)):
                    failures.append("fill_reference_mismatch")
            multiplier = plan["costs"]["buy_multiplier" if kind == "BUY_FILLED" else "sell_multiplier"]
            if (Decimal(str(p["multiplier"])) != Decimal(str(multiplier)) or
                    Decimal(str(p.get("execution_reference", p["raw_price"]))) != Decimal(str(p["raw_price"])) or
                    money(p["gross_value"]) != money(Decimal(str(p["raw_price"]))*p["quantity"]) or
                    money(p["cash_value"]) != money(Decimal(str(p["raw_price"]))*Decimal(str(multiplier))*p["quantity"])):
                failures.append("fill_cash_or_cost_mismatch")
        if kind == "DATA_UNRESOLVED":
            failures.append("unresolved_paper_path")
        if kind == "SESSION_MARKED":
            if money(p["cash"]) != state["cash"]:
                failures.append("marked_cash_mismatch")
            for ticker, position in state["positions"].items():
                obs = observations.get(ticker, {})
                if not obs.get("bar") or p["marks"].get(ticker) != obs["bar"]["close"]:
                    failures.append("missing_or_revised_close_mark")
            if p["nav"] is not None:
                value = state["cash"]+sum((Decimal(str(p["marks"].get(t, 0)))*pos["quantity"] for t, pos in state["positions"].items()), Decimal(0))
                if money(p["nav"]) != money(value):
                    failures.append("nav_cash_and_positions_mismatch")
            for ticker, recorded in p.get("observations", {}).items():
                current = observations.get(ticker, {})
                fields = ("bar", "actions_verified", "tradable", "buy_capacity", "sell_capacity", "action_id", "split_ratio", "previous_isin", "unsupported_action")
                if any(recorded.get(k) != current.get(k) for k in fields):
                    failures.append("observed_data_revision_requires_review")
            index = observations.get(registration["benchmark"], {})
            if not observe_problem(index, registration["benchmark"], session, True) and p["benchmark_base"]:
                expected_base = state["benchmark_base"] if state["benchmark_base"] is not None else index["bar"]["open"]
                if p["benchmark_base"] != expected_base:
                    failures.append("benchmark_open_base_mismatch")
                value = money(state["initial_cash"]*Decimal(str(index["bar"]["close"]))/Decimal(str(p["benchmark_base"])))
                if p["benchmark_nav"] is None or money(p["benchmark_nav"]) != value:
                    failures.append("benchmark_cash_flow_mismatch")
        reduce_event(state, kind, p)
    if instants != sorted(instants):
        failures.append("nonmonotonic_event_timeline")
    for plan_id, order in before_orders.items():
        plan = plans.get(plan_id)
        if order["lifecycle"] != "ARMED" or not plan or not plan["first_entry_session"] <= session <= plan["expiry_session"]:
            continue
        obs = observations.get(plan["ticker"], {})
        bar = obs.get("bar")
        problem = observe_problem(obs, plan["ticker"], session)
        if not problem and (obs.get("tradable") is None or obs.get("buy_capacity") is None):
            problem = "opening_capacity_or_tradability_unknown"
        fills = [e["payload"] for e in events if e["kind"] == "BUY_FILLED" and e["payload"]["plan_id"] == plan_id]
        dispositions = [e["payload"].get("reason") for e in events if e["payload"].get("plan_id") == plan_id and e["kind"] in {"ORDER_CANCELLED", "ORDER_EXPIRED", "DATA_UNRESOLVED"}]
        comparisons.append({"plan_id": plan_id, "ticker": plan["ticker"], "session": session,
            "entry_min": plan["entry_min"], "entry_max": plan["entry_max"], "reserved_quantity": order["quantity"],
            "observed_open": bar.get("open") if bar else None, "observed_buy_capacity": obs.get("buy_capacity"),
            "open_in_range": plan["entry_min"] <= bar["open"] <= plan["entry_max"] if bar and bar.get("open") is not None else None,
            "simulated_fill_quantity": sum(p["quantity"] for p in fills), "dispositions": dispositions,
            "status": "UNRESOLVED" if problem else "REFERENCE_CHECKED" if fills else "NO_SIMULATED_FILL",
            "problem": problem})
        if problem:
            failures.append("entry_comparison_unresolved:"+problem)
    return state, sorted(set(failures)), comparisons


class ForwardService:
    def __init__(self, config, clock, calendar, settings=None, paper=None, protected=()):
        self.config, self.clock, self.calendar = config, clock, calendar
        self.settings = settings or ForwardSettings()
        self.paper = paper or PaperService(config, clock, calendar)
        paths = [config.SWING_DB_PATH, config.SWING_PAPER_DB_PATH, config.SWING_ARTIFACT_DIR,
                 "data/history.db", "data/swing_ops.db", "data/swing_validation.db", *protected]
        if config.SWING_SOURCE_DB_PATH:
            paths.append(config.SWING_SOURCE_DB_PATH)
        if Path(self.settings.SWING_FORWARD_DB_PATH).resolve().is_relative_to(Path(config.SWING_ARTIFACT_DIR).resolve()):
            raise ValueError("Forward journal cannot be inside source artifacts")
        self.journal = ForwardJournal(self.settings, clock, paths, config.SQLITE_BUSY_TIMEOUT_SEC)

    def schedule(self, sessions):
        cal = self.calendar.calendar
        return {s: {"close": cal.session_close(s).isoformat(),
                    "next_open": cal.session_open(cal.next_session(s)).isoformat()} for s in sessions}

    def ledger_snapshot(self, account_id):
        with self.paper.ledger.connection() as conn:
            state = self.paper.ledger._state(conn, account_id)
            events = [{**dict(e), "payload": json.loads(e["payload"])} for e in conn.execute(
                "SELECT * FROM events WHERE account_id=? ORDER BY seq", (account_id,))]
            plans = {r[0]: json.loads(encoded(asdict(self.paper.ledger.plan(account_id, r[0], conn)))) for r in conn.execute(
                "SELECT id FROM plans WHERE account_id=? ORDER BY id", (account_id,))}
        return state, events, plans

    def register(self, campaign, account_id, start, end, hypothesis, key):
        if not key.strip():
            raise ValueError("A nonempty command key is required")
        request = {"operation": "register", "account_id": account_id, "start": str(start), "end": str(end), "hypothesis": hypothesis}
        old = self.journal.receipt(campaign, key, request)
        if old:
            return old
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", campaign) or not 20 <= len(hypothesis.strip()) <= 5000:
            raise ValueError("Campaign ID and a specific preregistered hypothesis required")
        cal, now = self.calendar.calendar, utc(self.clock.now())
        if start > end or not cal.is_session(str(start)) or not cal.is_session(str(end)) or cal.session_open(str(start)).to_pydatetime() <= now:
            raise ValueError("Ordered future exchange-session window required")
        sessions = [str(s.date()) for s in cal.sessions_in_range(start, end)]
        if len(sessions) < self.settings.SWING_FORWARD_MIN_SESSIONS:
            raise ValueError("Forward window needs at least the configured actual-session minimum")
        tail = max(self.config.SWING_BREAKOUT_MAX_HOLD, self.config.SWING_PULLBACK_MAX_HOLD)+self.config.SWING_ENTRY_VALID_SESSIONS+1
        if len(sessions) <= tail:
            raise ValueError("Window needs a signal period plus the frozen holding/entry wind-down")
        state, events, plans = self.ledger_snapshot(account_id)
        seed = str(cal.previous_session(str(start)).date())
        if len(events) != 1 or events[0]["kind"] != "ACCOUNT_FUNDED" or plans or state["last_session"] != seed:
            raise PaperConflict("Use an unused explicitly funded account seeded on the preceding session")
        if utc(datetime.fromisoformat(events[0]["created_at"])) > now:
            raise PaperConflict("Future account event")
        cfg = normalized(configuration(self.config))
        from app.swing.setups import implementation_hash
        return self.journal.register(campaign, account_id, key, request, {
            "id": campaign, **request, "sessions": sessions, "minimum_sessions": self.settings.SWING_FORWARD_MIN_SESSIONS,
            "configuration": cfg, "code_hashes": fingerprint(), "runtime": runtime_fingerprint(),
            "schedule": self.schedule(sessions),
            "signal_end": sessions[-tail-1], "wind_down_sessions": tail,
            "setup_implementation": implementation_hash(), "seed_state": state,
            "seed_head": events[-1]["event_hash"], "benchmark": self.config.SWING_BENCHMARK,
            "synthetic": synthetic(account_id)})

    def capture(self, campaign, vintage, key):
        if not key.strip():
            raise ValueError("A nonempty command key is required")
        request = {"operation": "capture", "vintage": vintage}
        old = self.journal.receipt(campaign, key, request)
        if old:
            return old
        material = self.journal.read(campaign)
        registration = material["registration"]
        now = utc(self.clock.now())
        cal = self.calendar.calendar
        session = last_completed(self.calendar, now)
        if session not in registration["sessions"] or not (cal.session_close(session).to_pydatetime() <= now < cal.session_open(cal.next_session(session)).to_pydatetime()):
            raise ValueError("Capture only after this campaign session closes and before the next open")
        previous = material["days"][-1]["payload"] if material["days"] else None
        previous_state = previous["state"] if previous else registration["seed_state"]
        previous_head = previous["ledger_head"] if previous else registration["seed_head"]
        previous_version = previous["ledger_version"] if previous else registration["seed_state"]["version"]
        previous_at = previous["recorded_at"] if previous else registration["registered_at"]
        if now < utc(datetime.fromisoformat(previous_at)):
            raise PaperConflict("Forward clock moved backwards")
        state, all_events, all_plans = self.ledger_snapshot(registration["account_id"])
        if len(all_events) < previous_version or all_events[previous_version-1]["event_hash"] != previous_head:
            raise PaperConflict("Paper ledger diverged from sealed event head")
        events = all_events[previous_version:]
        prior_plans = {p for d in material["days"] for p in d["payload"]["new_plans"]}
        new_plans = {k: v for k, v in all_plans.items() if k not in prior_plans}
        metadata, header = SwingResearchService(self.paper.repository, self.config, self.clock, self.calendar).snapshot(vintage, now)
        audit = audit_vintage(self.paper.repository, vintage)
        active_orders = [o["ticker"] for book in (state, previous_state) for o in book["orders"].values()
                         if o["lifecycle"] in {"CANDIDATE", "ARMED", "UNRESOLVED"}]
        event_tickers = [e["payload"]["ticker"] for e in events if e["payload"].get("ticker")]
        tickers = {self.config.SWING_BENCHMARK, *state["positions"], *previous_state["positions"],
                   *active_orders, *event_tickers, *[p["ticker"] for p in new_plans.values()]}
        observations = {}
        for ticker in sorted(tickers):
            obs = self.paper.market.observe(ticker, date.fromisoformat(session), vintage, now)
            observations[ticker] = json.loads(encoded({**asdict(obs),
                "bar": obs.bar.model_dump(mode="json") if obs.bar else None}))
        reconstructed, failures, comparisons = reconcile(registration, previous_state, all_plans, events, observations, session, now, self.calendar)
        if any(utc(datetime.fromisoformat(e["created_at"])) < utc(datetime.fromisoformat(previous_at)) for e in events):
            failures.append("events_backdated_before_previous_capture")
        if any(p["evidence"]["vintage"] != vintage for p in new_plans.values()):
            failures.append("new_plan_source_vintage_mismatch")
        failures.extend(audit["failures"])
        for ticker, obs in observations.items():
            problem = observe_problem(obs, ticker, session, ticker == self.config.SWING_BENCHMARK)
            if problem:
                failures.append("observation_unresolved:"+ticker+":"+problem)
            if utc(datetime.fromisoformat(obs["available_at"])) > now:
                failures.append("future_observation")
        if fingerprint() != registration["code_hashes"] or runtime_fingerprint() != registration["runtime"] or normalized(configuration(self.config)) != registration["configuration"]:
            failures.append("frozen_implementation_or_configuration_drift")
        if self.schedule(registration["sessions"]) != registration["schedule"]:
            failures.append("calendar_schedule_drift")
        if digest(reconstructed | {"version": state["version"]}) != digest(state):
            failures.append("independent_state_reconstruction_mismatch")
        mark = next((m for m in state["nav"] if m["session"] == session), None)
        if state["last_session"] != session or mark is None or mark.get("nav") is None or mark.get("benchmark_nav") is None:
            failures.append("missing_exact_session_nav_or_benchmark")
        if state["unresolved"]:
            failures.append("unresolved_paper_liabilities")
        if synthetic([header, all_plans, observations, registration["account_id"]]):
            failures.append("synthetic_evidence")
        payload = {"session": session, "recorded_at": now.isoformat(), "vintage": vintage,
            "audit": audit, "source_header_hash": digest(header), "source_imported_at": metadata["imported_at"],
            "ledger_version": state["version"], "ledger_head": all_events[-1]["event_hash"],
            "events": events, "new_plans": new_plans, "observations": observations,
            "comparisons": comparisons, "state": state, "failures": sorted(set(failures)),
            "synthetic": registration["synthetic"] or synthetic([header, all_plans, observations])}
        return self.journal.append(campaign, key, request, material["head"], json.loads(encoded(payload)))

    def report(self, campaign):
        material = self.journal.read(campaign)
        registration = material["registration"]
        days = [r["payload"] for r in material["days"]]
        completed = last_completed(self.calendar, self.clock.now())
        expected = [s for s in registration["sessions"] if s <= completed]
        recorded = {d["session"] for d in days}
        missing = [s for s in expected if s not in recorded]
        failures = sorted({f for d in days for f in d["failures"]})
        if missing:
            failures.append("missing_daily_captures")
        if len(days) < registration["minimum_sessions"] or completed < registration["end"]:
            failures.append("minimum_prospective_window_incomplete")
        if (fingerprint() != registration["code_hashes"] or runtime_fingerprint() != registration["runtime"] or
                normalized(configuration(self.config)) != registration["configuration"]):
            failures.append("frozen_implementation_or_runtime_drift")
        if self.schedule(registration["sessions"]) != registration["schedule"]:
            failures.append("calendar_schedule_drift")
        if any(utc(datetime.fromisoformat(d["recorded_at"])) > utc(self.clock.now()) for d in days):
            failures.append("future_journal_record")
        current, events, _ = self.ledger_snapshot(registration["account_id"])
        sealed_head = days[-1]["ledger_head"] if days else registration["seed_head"]
        if events[-1]["event_hash"] != sealed_head:
            failures.append("unreconciled_ledger_events")
        if days and current["last_session"] != days[-1]["session"]:
            failures.append("ledger_session_ahead_of_monitor")
        for vintage in {d["vintage"] for d in days}:
            try:
                self.paper.repository.verify(vintage)
            except (ValueError, OSError):
                failures.append("source_evidence_unavailable_or_modified")
        final = days[-1]["state"] if days else registration["seed_state"]
        if final["positions"] or any(o["lifecycle"] in {"ARMED", "UNRESOLVED"} for o in final["orders"].values()):
            failures.append("open_or_unresolved_final_book")
        if registration["synthetic"] or any(d["synthetic"] for d in days):
            failures.append("synthetic_evidence")
        return {"campaign": campaign, "account_id": registration["account_id"], "model_status": "RESEARCH_ONLY",
            "paper_eligible": False, "promotion_allowed": False, "operational_gate_passed": not failures,
            "independent_validation_required": True, "minimum_sessions": registration["minimum_sessions"],
            "registered_at": registration["registered_at"], "start": registration["start"], "end": registration["end"],
            "signal_end": registration["signal_end"], "wind_down_sessions": registration["wind_down_sessions"],
            "captured_sessions": len(days), "reconciled_sessions": sum(not d["failures"] for d in days),
            "missing_sessions": missing, "failures": sorted(set(failures)), "journal_head": material["head"],
            "days": [{k: d[k] for k in ("session", "recorded_at", "vintage", "ledger_head", "failures", "comparisons")} for d in days],
            "nav": [next((n for n in d["state"]["nav"] if n["session"] == d["session"]), None) for d in days],
            "resolved_trades": sum(c["outcome_status"] == "RESOLVED" for c in final["closed"]),
            "unresolved_records": len(final["unresolved"]),
            "notice": "Observed OHLC references versus simulated fills, not broker confirmations. Thirty sessions alone do not establish an edge."}

    def campaigns(self):
        return [self.report(m["registration"]["id"]) for m in self.journal.campaigns()
                if not m["registration"]["synthetic"] and not any(d["payload"]["synthetic"] for d in m["days"])]

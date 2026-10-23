"""Read-only S5 presentation service; the UI never reads stores/providers directly."""
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
from functools import wraps
import csv
import io
import json
from pathlib import Path

from app.providers.swing_archive import last_completed
from app.swing.models import DailyPrice, utc
from app.swing.paper_service import PaperService
from app.swing.repository import SwingRepository
from app.swing.service import SwingResearchService, UnknownVintage
from app.swing.setups import priority
from app.swing.explanations import view_summary


def filtered_plans(plans, strategy=None, sector=None, search="", names=None):
    needle = search.strip().casefold()
    return sorted([p for p in plans if (not strategy or p.strategy == strategy) and
                   (not sector or p.evidence.sector == sector) and
                   (not needle or needle in (p.ticker+" "+(names or {}).get(p.ticker, "")+" "+p.rationale).casefold())], key=priority)


EXPORT_FIELDS = ("id", "ticker", "name", "strategy", "session", "decision_at", "model_status", "lifecycle", "vintage",
                 "price_basis", "reference_close", "entry_min", "entry_max", "first_entry_session", "expiry_session",
                 "stop", "target", "net_rr", "sector", "quantity", "missing_gates")


def plans_csv(plans, names=None):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    for p in plans:
        row = {k: getattr(p, k, None) for k in EXPORT_FIELDS}
        row.update(name=(names or {}).get(p.ticker), vintage=p.evidence.vintage, reference_close=p.features.close, sector=p.evidence.sector,
                   missing_gates=";".join(p.missing_gates), quantity=None)
        # Spreadsheet formulas in source text must not execute when opening CSV.
        row = {k: "'"+v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for k, v in row.items()}
        writer.writerow(row)
    return output.getvalue()


def _verified_request(operation):
    """Keep verification reuse inside a single off-event-loop workspace read."""
    @wraps(operation)
    def read(self, *args, **kwargs):
        with self.repository.verification_scope():
            return operation(self, *args, **kwargs)
    return read


class SwingWorkspace:
    def __init__(self, config, clock, calendar, operations, registry):
        self.config, self.clock, self.calendar = config, clock, calendar
        self.operations, self.registry = operations, registry
        self.repository = SwingRepository(Path(config.SWING_DB_PATH), config.SQLITE_BUSY_TIMEOUT_SEC, calendar)
        self.research = SwingResearchService(self.repository, config, clock, calendar)
        self.paper = PaperService(config, clock, calendar)

    @staticmethod
    def synthetic(metadata):
        return "SYNTHETIC" in str(metadata.get("source_uri", "")).upper() or "SYNTHETIC" in str(metadata.get("header", "")).upper()

    def snapshot(self, vintage):
        metadata, header = self.research.snapshot(vintage, utc(self.clock.now()))
        if self.synthetic(metadata):
            raise ValueError("Synthetic evidence cannot be served by the workspace")
        return metadata, header

    def vintages(self):
        if not self.repository.path.exists():
            return []
        with self.repository.connection() as conn:
            rows = conn.execute("SELECT id,source_uri,header,imported_at FROM snapshots ORDER BY imported_at DESC").fetchall()
        result = []
        for row in rows:
            if self.synthetic(dict(row)) or datetime.fromisoformat(row["imported_at"]) > utc(self.clock.now()):
                continue
            # Selected evidence is verified by snapshot(); discovery is metadata only.
            result.append({"vintage": row["id"], "imported_at": row["imported_at"],
                           "source": "Local immutable archive", "verified_on_selection": False})
        return result

    @_verified_request
    def ideas(self, vintage, strategy=None, sector=None, search=""):
        metadata, header = self.snapshot(vintage)
        names = self.names(metadata, header)
        if not self.config.SWING_ENABLED:
            result = {"market": "NSE", "vintage": vintage, "model_status": "RESEARCH_ONLY", "paper_eligible": False,
                    "plans": [], "failed": [], "filtered": [], "funnel": {}, "missing_gates": ["swing_disabled"], "view_count": 0, "names": names}
            result["rule_explanations"] = []
            result["chart_contexts"] = []
            result["view_summary"] = view_summary(result, 0, enabled=False)
            return result
        result = self.research.results(vintage)
        matching = filtered_plans(result["plans"], strategy, sector, search, names)
        result["view_summary"] = view_summary(result, len(matching))
        result["plans"] = matching
        result["names"] = names
        result["view_count"] = len(result["plans"])
        return result

    def names(self, metadata, header):
        content = self.repository.universe_bytes(metadata["id"]).decode("utf-8-sig")
        return {r["Symbol"].strip()+".NS": r["Company Name"].strip() for r in
                csv.DictReader(line for line in io.StringIO(content) if not line.lstrip().startswith("#"))}

    @_verified_request
    def view(self, vintage, strategy=None, sector=None, search=""):
        metadata, header = self.snapshot(vintage)
        result = self.ideas(vintage, strategy, sector, search)
        quality = self.repository.report(vintage)
        from app.swing.validation import audit_vintage
        audit = audit_vintage(self.repository, vintage)
        completed = last_completed(self.calendar, self.clock.now())
        failures = Counter()
        for item in result["failed"]:
            for reason in item.reasons if hasattr(item, "reasons") else item["reasons"]:
                failures[reason] += 1
        return {"research": result, "export_csv": plans_csv(result["plans"], result.get("names")), "source": {"vintage": vintage, "imported_at": metadata["imported_at"],
            "coverage_session": quality.get("last_completed_session"), "required_session": completed,
            "stale": quality.get("last_completed_session") != completed or not quality["summary"].get("complete_trailing_price_histories"),
            "summary": quality["summary"], "benchmark": {k: quality["benchmark"].get(k) for k in ("ticker", "rows", "open_rows", "status")},
            "members": [{"ticker": m["ticker"], "current_series": m.get("current_series"), "latest_price_session": m.get("latest_price"),
                         "price_rows": m.get("price_rows", 0), "delivery_status": m.get("delivery_status", "unavailable"),
                         "missing_price_session_count": len(m.get("missing_price_sessions", []))} for m in quality.get("members", [])],
            "limitations": quality.get("limitations", []), "blockers": audit["failures"], "failure_counts": dict(failures)},
            "controls": self.operations.controls(), "scope": "Daily swing research only; no four-hour or broker research mixed in"}

    def chart(self, vintage, ticker, bars):
        self.snapshot(vintage)
        if not 20 <= bars <= self.operations.settings.SWING_CHART_BARS:
            raise ValueError("Requested chart range exceeds configured observation limit")
        end = date.fromisoformat(last_completed(self.calendar, self.clock.now()))
        dates = [s.date() for s in self.calendar.calendar.sessions if s.date() <= end][-bars:]
        with self.repository.connection() as conn:
            rows = conn.execute("SELECT payload FROM prices WHERE snapshot_id=? AND ticker=? AND session>=? AND session<=? ORDER BY session",
                                (vintage, ticker, str(dates[0]), str(end))).fetchall()
        observations = [DailyPrice.model_validate_json(r[0]) for r in rows]
        bases = sorted({r.price_basis for r in observations})
        return {"vintage": vintage, "ticker": ticker, "price_basis": bases[0] if len(bases) == 1 else "mixed" if bases else "unavailable",
                "observations": [r.model_dump(mode="json") for r in observations],
                "missing_sessions": [str(s) for s in dates if s not in {r.session for r in observations}],
                "trade_usable": False, "notice": "Observed archive prices for inspection. Unknown basis is not raw; missing sessions are not filled."}

    def accounts(self):
        if not self.paper.ledger.path.exists():
            return []
        with self.paper.ledger.connection() as conn:
            return [row[0] for row in conn.execute("SELECT id FROM accounts ORDER BY id") if "SYNTHETIC" not in row[0].upper()]

    @_verified_request
    def allocation(self, vintage, account_id, strategy=None, sector=None, search=""):
        metadata, header = self.snapshot(vintage)
        if not self.config.SWING_ENABLED:
            return {"allocations": [], "missing_gates": ["Swing research is disabled"]}
        from app.core.config import Settings
        from app.swing.risk import allocate
        from app.swing.replay import ReplayEngine
        state = self.paper.ledger.state(account_id)
        current = date.fromisoformat(last_completed(self.calendar, self.clock.now()))
        if state["last_session"] != str(current) or state["unresolved"]:
            return {"allocations": [], "missing_gates": ["Account must have current, resolved close marks before sizing"]}
        nav = state["nav"][-1]["nav"] if state["nav"] else str(state["cash"])
        if nav is None:
            return {"allocations": [], "missing_gates": ["Current NAV unavailable"]}
        cfg = Settings(_env_file=None, **{**self.config.model_dump(), "SWING_CAPITAL_INR": float(nav)})
        research = SwingResearchService(self.repository, cfg, self.clock, self.calendar).results(vintage)
        plans = filtered_plans(research["plans"], strategy, sector, search, self.names(metadata, header))
        engine = ReplayEngine(self.paper.ledger, self.paper.market, self.calendar, cfg, self.clock)
        book = engine._book(state, current)
        tickers = {p.ticker for p in plans} | {i.ticker for i in book.items}
        returns = {t: self.paper.market.returns(t, current, cfg.SWING_CORRELATION_SESSIONS, vintage, self.clock.now()) for t in tickers}
        expected = tuple(s.date() for s in self.calendar.calendar.sessions if s.date() <= current)[-cfg.SWING_CORRELATION_SESSIONS:]
        allocations = allocate(tuple(plans), cfg, book, returns, expected, calendar=self.calendar, dynamic_nav=True)
        return {"allocations": [asdict(a) for a in allocations], "missing_gates": research["missing_gates"],
                "notice": "Read-only hypothetical allocation; reserves no cash and creates no orders"}

    def validation_reports(self):
        if not self.registry.path.exists():
            return []
        with self.registry.connection() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM reports ORDER BY rowid DESC LIMIT 100")]
        reports = []
        for report_id in ids:
            record = self.validation_report(report_id)
            if record is not None:
                p = record["payload"]
                reports.append({"id": report_id, "study_id": p["study_id"], "phase": p["phase"],
                                "model_status": p["model_status"], "authentication": record["authentication"], "failures": len(p["failures"])})
        return reports

    def validation_report(self, report_id):
        record = self.registry.report(report_id)
        p = record["payload"]
        if "SYNTHETIC" in p.get("study_id", "").upper() or any(x.get("synthetic") for x in p.get("input_manifest", [])):
            return None
        return record

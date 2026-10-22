"""Durable research queue orchestration; no account is created implicitly."""
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.core.config import Settings
from app.providers.swing_archive import file_hash, last_completed
from app.swing.execution import ArchiveReplayMarket
from app.swing.ledger import PaperLedger, encoded, money
from app.swing.ledger import PaperConflict, PaperNotFound
from app.swing.replay import ReplayEngine
from app.swing.repository import SwingRepository
from app.swing.service import SwingResearchService
from app.swing.setups import digest


class PaperService:
    def __init__(self, config, clock, calendar):
        self.config, self.clock, self.calendar = config, clock, calendar
        self.ledger = PaperLedger(Path(config.SWING_PAPER_DB_PATH), clock, config.SQLITE_BUSY_TIMEOUT_SEC)
        self.repository = SwingRepository(Path(config.SWING_DB_PATH), config.SQLITE_BUSY_TIMEOUT_SEC, calendar)
        self.market = ArchiveReplayMarket(self.repository, calendar)
        self.operations = None

    def create_account(self, account_id, capital, session, key):
        if self.operations and self.operations.controls()["paused"]:
            known = False
            if self.ledger.path.exists():
                with self.ledger.connection() as conn:
                    known = conn.execute("SELECT 1 FROM commands WHERE account_id=? AND key=?", (account_id, key)).fetchone() is not None
            if not known:
                self.operations.require_new_work()
        if not self.calendar.calendar.is_session(str(session)) or session > date.fromisoformat(last_completed(self.calendar, self.clock.now())):
            raise ValueError("Funding session must be a completed exchange session")
        return self.ledger.create_account(account_id, capital, session, key)

    def enqueue(self, account_id, key, vintage, through, expected_version, management_only=False):
        request = {"vintage": vintage, "through": str(through), "expected_version": expected_version}
        if management_only:
            request["management_only"] = True
        old = self.ledger.existing_job(account_id, key, request)
        if old:
            return old
        if self.operations and not management_only:
            self.operations.require_new_work()
        if through > date.fromisoformat(last_completed(self.calendar, self.clock.now())) or not self.calendar.calendar.is_session(str(through)):
            raise ValueError("Replay through-date must be a completed exchange session")
        # Explicit availability/hash verification before accepting durable work.
        SwingResearchService(self.repository, self.config, self.clock, self.calendar).snapshot(vintage, self.clock.now())
        return self.ledger.enqueue(account_id, key, request)

    def cancel(self, account_id, plan_id, key, expected_version, reason):
        def apply(conn, state, emit):
            order = state["orders"].get(plan_id)
            if not order:
                raise PaperNotFound("Unknown order")
            if order["lifecycle"] not in {"CANDIDATE", "ARMED"}:
                raise PaperConflict("Open or unresolved liability cannot be cancelled as unfilled")
            emit("ORDER_CANCELLED", {"plan_id": plan_id, "reason": reason, "session": state["last_session"]})
            return {"plan_id": plan_id, "lifecycle": "CANCELLED"}
        request = {"operation": "cancel", "plan_id": plan_id, "reason": reason, "expected_version": expected_version}
        return self.ledger.command(account_id, key, expected_version, request, apply)

    def next_job(self):
        if not self.config.SWING_ENABLED or not self.config.SWING_REPLAY_ENABLED:
            return None
        if self.operations and self.operations.controls()["paused"]:
            return self.ledger.next_job(management_only=True)
        return self.ledger.next_job()

    def process(self, job):
        try:
            stored = self.ledger.job(job["id"])
            if stored["status"] != "QUEUED":
                return stored["result"] if stored["status"] == "DONE" else {"status": "FAILED", "error": stored["error"]}
            management = job["request"].get("management_only", False)
            if self.operations and self.operations.controls()["paused"] and not management:
                return {"status": "PAUSED"}  # keep accepted work queued, not failed
            state = self.ledger.state(job["account_id"])
            # Only a reconciled, observed book can contribute NAV to a new plan.
            nav = (state["nav"][-1]["nav"] if state["nav"] else str(state["cash"]))
            cfg = Settings(_env_file=None, **{**self.config.model_dump(), "SWING_CAPITAL_INR": float(nav) if nav else None})
            research = ({"plans": [], "funnel": {}, "missing_gates": ["management_only_no_new_entries"]} if management else
                        SwingResearchService(self.repository, cfg, self.clock, self.calendar).results(job["request"]["vintage"]))
            engine = ReplayEngine(self.ledger, self.market, self.calendar, self.config, self.clock)
            def apply(conn, account, emit):
                captured = self.ledger.capture(conn, account["account_id"], research["plans"], emit)
                result = engine.advance(conn, account, emit, job["request"]["vintage"], date.fromisoformat(job["request"]["through"]), allow_new_entries=not management)
                root = Path(__file__).parent
                result.update(captured_plan_ids=captured, source_funnel=research["funnel"],
                              missing_gates=research["missing_gates"], implementation_hash=digest({
                                  p: file_hash(root/p) for p in ("ledger.py", "execution.py", "replay.py", "paper_service.py", "risk.py")}))
                return result
            return self.ledger.command(job["account_id"], job["key"], job["request"]["expected_version"], job["request"], apply)
        except Exception as exc:
            self.ledger.record_job_error(job["id"], type(exc).__name__)
            return {"status": "FAILED", "error": type(exc).__name__}

    def performance(self, account_id):
        state = self.ledger.state(account_id)
        resolved = [p for p in state["closed"] if p["outcome_status"] == "RESOLVED"]
        pnl = sum((money(p["net_pnl"]) for p in resolved), Decimal(0))
        return {"account_id": account_id, "model_status": "RESEARCH_ONLY", "mode": "RESEARCH_REPLAY",
                "resolved_trades": len(resolved), "unresolved_trades": len(state["closed"])-len(resolved),
                "resolved_net_pnl": str(money(pnl)), "open_positions": len(state["positions"]),
                "unresolved_records": len(state["unresolved"]), "equity": state["nav"],
                "closed": state["closed"], "ambiguity_sensitivity": self.ledger.ambiguity_sensitivity(account_id),
                "notice": "OHLC approximations; descriptive research only, not validated performance"}

"""Append-only research accounts, frozen plans, jobs and hash-chained events."""
from contextlib import contextmanager, closing
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN
import hashlib
import json
from pathlib import Path
import sqlite3

from pydantic import TypeAdapter

from app.swing.models import utc
from app.swing.plans import Plan
from app.swing.setups import digest


class PaperConflict(ValueError):
    pass


class PaperNotFound(ValueError):
    pass


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def money(value, *, reserve=False):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Finite INR amount required")
    return result.quantize(Decimal("0.01"), rounding=ROUND_CEILING if reserve else ROUND_HALF_EVEN)


def empty_state(account_id):
    return {"account_id": account_id, "mode": "RESEARCH_REPLAY", "model_status": "RESEARCH_ONLY",
            "paper_eligible": False, "version": 0, "cash": Decimal(0), "initial_cash": Decimal(0),
            "last_session": None, "orders": {}, "positions": {}, "closed": [], "nav": [],
            "unresolved": [], "actions": [], "benchmark_base": None}


def reduce_event(state, kind, payload):
    """Reconstruct all financial state from domain events; no balance cache."""
    p = deepcopy(payload)
    orders, positions = state["orders"], state["positions"]
    if kind == "ACCOUNT_FUNDED":
        state["cash"] = state["initial_cash"] = money(p["capital"])
        state["last_session"] = p["session"]
    elif kind == "PLAN_CAPTURED":
        orders[p["plan_id"]] = {**p, "lifecycle": "CANDIDATE"}
    elif kind == "ORDER_RESERVED":
        orders[p["plan_id"]].update(p, lifecycle="ARMED")
    elif kind in {"ORDER_CANCELLED", "ORDER_EXPIRED"}:
        orders[p["plan_id"]].update(p, lifecycle="CANCELLED" if kind.endswith("CANCELLED") else "EXPIRED")
    elif kind == "BUY_FILLED":
        state["cash"] -= money(p["cash_value"])
        orders[p["plan_id"]].update(lifecycle="OPEN", filled_quantity=p["quantity"],
                                    cancelled_remainder=p["cancelled_remainder"])
        positions[p["ticker"]] = p["position"]
    elif kind == "SELL_FILLED":
        position = positions[p["ticker"]]
        if not 0 < p["quantity"] <= position["quantity"]:
            raise PaperConflict("Invalid event exit quantity")
        state["cash"] += money(p["cash_value"])
        position["quantity"] -= p["quantity"]
        position["proceeds"] = str(money(position["proceeds"]) + money(p["cash_value"]))
        if position["quantity"] == 0:
            orders[position["plan_id"]]["lifecycle"] = "CLOSED"
            state["closed"].append({**position, "exit_session": p["session"], "exit_reason": p["reason"],
                                    "net_pnl": str(money(position["proceeds"]) - money(position["entry_cash"])),
                                    "outcome_status": "UNRESOLVED_PATH" if position["uncertain"] else "RESOLVED"})
            del positions[p["ticker"]]
    elif kind == "EXIT_PENDING":
        positions[p["ticker"]]["exit_pending"] = p["reason"]
    elif kind == "SPLIT_APPLIED":
        positions[p["ticker"]].update(p["position_changes"])
        state["actions"].append(p["action_key"])
    elif kind == "DATA_UNRESOLVED":
        state["unresolved"].append(p)
        if p.get("plan_id") in orders and orders[p["plan_id"]]["lifecycle"] == "ARMED":
            orders[p["plan_id"]]["lifecycle"] = "UNRESOLVED"
        if p.get("ticker") in positions:
            positions[p["ticker"]]["uncertain"] = True
            if "action" in p["reason"] or "identity" in p["reason"]:
                positions[p["ticker"]]["action_unresolved"] = True
    elif kind == "SESSION_MARKED":
        for ticker, value in p["marks"].items():
            if ticker in positions:
                positions[ticker]["mark"] = value
                positions[ticker]["mark_session"] = p["session"]
        state["last_session"] = p["session"]
        state["benchmark_base"] = p["benchmark_base"]
        state["nav"].append(p)
    elif kind not in {"ALLOCATION_REJECTED", "AMBIGUOUS_EXIT", "REPLAY_RECORDED"}:
        raise PaperConflict("Unsupported event type")
    if state["cash"] < 0:
        raise PaperConflict("Negative research cash")
    reserved = sum((money(o["reserved_cash"]) for o in orders.values()
                    if o["lifecycle"] in {"ARMED", "UNRESOLVED"}), Decimal(0))
    if reserved > state["cash"]:
        raise PaperConflict("Reservations exceed available settled cash")


class PaperLedger:
    def __init__(self, path: Path, clock, timeout=30):
        self.path, self.clock, self.timeout = Path(path), clock, timeout

    @contextmanager
    def connection(self, *, write=False):
        if not write and not self.path.exists():
            raise FileNotFoundError(self.path)
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path.resolve().as_uri()+('?mode=rwc' if write else '?mode=ro'),
                                    uri=True, timeout=self.timeout, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            if not write:
                conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                schema = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if not schema and write:
                    self._initialize(conn)
                elif not any(row[0] == "paper_schema" for row in schema):
                    raise PaperConflict("Not a paper ledger; refusing migration")
                if tuple(conn.execute("SELECT max(version),count(*) FROM paper_schema").fetchone()) != (1, 1):
                    raise PaperConflict("Unsupported paper ledger schema")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @staticmethod
    def _initialize(conn):
        statements = (
            "CREATE TABLE paper_schema(version INTEGER PRIMARY KEY)",
            "INSERT INTO paper_schema VALUES(1)",
            "CREATE TABLE accounts(id TEXT PRIMARY KEY,created_at TEXT NOT NULL)",
            """CREATE TABLE plans(account_id TEXT NOT NULL REFERENCES accounts(id),id TEXT NOT NULL,
                logical_key TEXT NOT NULL,payload TEXT NOT NULL,payload_hash TEXT NOT NULL,
                PRIMARY KEY(account_id,id),UNIQUE(account_id,logical_key))""",
            """CREATE TABLE events(account_id TEXT NOT NULL REFERENCES accounts(id),seq INTEGER NOT NULL,
                command_key TEXT NOT NULL,kind TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL,
                prev_hash TEXT NOT NULL,event_hash TEXT NOT NULL,PRIMARY KEY(account_id,seq))""",
            """CREATE TABLE commands(account_id TEXT NOT NULL REFERENCES accounts(id),key TEXT NOT NULL,
                request_hash TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(account_id,key))""",
            """CREATE TABLE jobs(id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES accounts(id),
                key TEXT NOT NULL,request TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
                UNIQUE(account_id,key))""",
            "CREATE TABLE job_errors(job_id TEXT PRIMARY KEY REFERENCES jobs(id),code TEXT NOT NULL,created_at TEXT NOT NULL)",
        )
        for sql in statements:
            conn.execute(sql)
        for table in ("accounts", "plans", "events", "commands", "jobs", "job_errors", "paper_schema"):
            for action in ("UPDATE", "DELETE"):
                conn.execute(f"CREATE TRIGGER immutable_{table}_{action} BEFORE {action} ON {table} "
                             "BEGIN SELECT RAISE(ABORT,'Paper ledger is append-only'); END")

    def _state(self, conn, account_id):
        if conn.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
            raise PaperNotFound("Unknown research account")
        state, previous = empty_state(account_id), ""
        for event in conn.execute("SELECT * FROM events WHERE account_id=? ORDER BY seq", (account_id,)):
            material = {k: event[k] for k in ("account_id", "seq", "command_key", "kind", "created_at", "payload", "prev_hash")}
            if (event["seq"] != state["version"]+1 or event["prev_hash"] != previous
                    or digest(material) != event["event_hash"]):
                raise PaperConflict("Broken event sequence/hash chain")
            reduce_event(state, event["kind"], json.loads(event["payload"]))
            state["version"], previous = event["seq"], event["event_hash"]
        return state

    def _emitter(self, conn, state, key):
        def emit(kind, payload):
            previous = conn.execute("SELECT event_hash FROM events WHERE account_id=? ORDER BY seq DESC LIMIT 1",
                                    (state["account_id"],)).fetchone()
            material = {"account_id": state["account_id"], "seq": state["version"]+1,
                        "command_key": key, "kind": kind, "created_at": utc(self.clock.now()).isoformat(),
                        "payload": encoded(payload), "prev_hash": previous[0] if previous else ""}
            reduce_event(state, kind, json.loads(material["payload"]))
            conn.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", (*material.values(), digest(material)))
            state["version"] += 1
        return emit

    def create_account(self, account_id, capital, session: date, key):
        amount = money(capital)
        if amount <= 0 or amount != Decimal(str(capital)) or not account_id or not key:
            raise ValueError("Positive explicit funding in paise, account and command key required")
        request = {"operation": "fund", "capital": str(amount), "session": session.isoformat()}
        with self.connection(write=True) as conn:
            receipt = conn.execute("SELECT * FROM commands WHERE account_id=? AND key=?", (account_id, key)).fetchone()
            if receipt:
                if receipt["request_hash"] != digest(request):
                    raise PaperConflict("Idempotency key reused with different content")
                return json.loads(receipt["result"])
            if conn.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
                raise PaperConflict("Account already funded; no implicit reset")
            conn.execute("INSERT INTO accounts VALUES(?,?)", (account_id, utc(self.clock.now()).isoformat()))
            state = empty_state(account_id)
            self._emitter(conn, state, key)("ACCOUNT_FUNDED", request)
            result = {"account_id": account_id, "version": state["version"], "mode": "RESEARCH_REPLAY"}
            conn.execute("INSERT INTO commands VALUES(?,?,?,?)", (account_id, key, digest(request), encoded(result)))
            return result

    def command(self, account_id, key, expected_version, request, handler):
        with self.connection(write=True) as conn:
            pending = conn.execute("SELECT request_hash FROM jobs WHERE account_id=? AND key=?", (account_id, key)).fetchone()
            if pending and pending[0] != digest(request):
                raise PaperConflict("Command key already belongs to a different accepted job")
            receipt = conn.execute("SELECT * FROM commands WHERE account_id=? AND key=?", (account_id, key)).fetchone()
            if receipt:
                if receipt["request_hash"] != digest(request):
                    raise PaperConflict("Idempotency key reused with different content")
                return json.loads(receipt["result"])
            state = self._state(conn, account_id)
            if state["version"] != expected_version:
                raise PaperConflict("Account version changed")
            result = handler(conn, state, self._emitter(conn, state, key)) or {}
            result.update(account_id=account_id, version=state["version"], mode="RESEARCH_REPLAY")
            conn.execute("INSERT INTO commands VALUES(?,?,?,?)", (account_id, key, digest(request), encoded(result)))
            return result

    def capture(self, conn, account_id, plans, emit):
        captured = []
        for plan in plans:
            if plan.id != digest(asdict(replace(plan, id=""))) or plan.model_status != "RESEARCH_ONLY" or plan.paper_eligible:
                raise PaperConflict("Invalid or promoted research plan")
            if (utc(plan.decision_at) > utc(self.clock.now()) or utc(plan.evidence.available_at) > utc(plan.decision_at)
                    or plan.lifecycle != "CANDIDATE" or plan.quantity is not None):
                raise PaperConflict("Plan availability/lifecycle cannot be fabricated or backdated")
            logical = digest((plan.ticker, plan.strategy, plan.session, plan.config_hash))
            old = conn.execute("SELECT id,payload FROM plans WHERE account_id=? AND logical_key=?", (account_id, logical)).fetchone()
            if old:
                if json.loads(old["payload"])["data_hash"] != plan.data_hash:
                    raise PaperConflict("Conflicting data revision for captured plan")
                captured.append(old["id"])
                continue
            payload = encoded(asdict(plan))
            conn.execute("INSERT INTO plans VALUES(?,?,?,?,?)", (account_id, plan.id, logical, payload, digest(payload)))
            emit("PLAN_CAPTURED", {"plan_id": plan.id, "ticker": plan.ticker, "session": plan.session.isoformat()})
            captured.append(plan.id)
        return captured

    def plan(self, account_id, plan_id, conn=None):
        if conn is None:
            with self.connection() as handle:
                return self.plan(account_id, plan_id, handle)
        row = conn.execute("SELECT * FROM plans WHERE account_id=? AND id=?", (account_id, plan_id)).fetchone()
        if row is None:
            raise PaperNotFound("Unknown captured plan")
        if digest(row["payload"]) != row["payload_hash"]:
            raise PaperConflict("Plan content hash mismatch")
        return TypeAdapter(Plan).validate_json(row["payload"])

    def state(self, account_id):
        with self.connection() as conn:
            state = self._state(conn, account_id)
        reserved = sum((money(o["reserved_cash"]) for o in state["orders"].values()
                        if o["lifecycle"] in {"ARMED", "UNRESOLVED"}), Decimal(0))
        state.update(reserved_cash=reserved, available_cash=state["cash"]-reserved)
        return state

    def events(self, account_id, after=0, limit=100):
        with self.connection() as conn:
            self._state(conn, account_id)
            return [dict(row) | {"payload": json.loads(row["payload"])} for row in conn.execute(
                "SELECT * FROM events WHERE account_id=? AND seq>? ORDER BY seq LIMIT ?", (account_id, after, limit))]

    def enqueue(self, account_id, key, request):
        with self.connection(write=True) as conn:
            state = self._state(conn, account_id)
            old = conn.execute("SELECT * FROM jobs WHERE account_id=? AND key=?", (account_id, key)).fetchone()
            if old:
                if old["request_hash"] != digest(request):
                    raise PaperConflict("Job key reused with different content")
                return old["id"]
            if state["version"] != request["expected_version"]:
                raise PaperConflict("Account version changed before acceptance")
            if conn.execute("SELECT 1 FROM commands WHERE account_id=? AND key=?", (account_id, key)).fetchone():
                raise PaperConflict("Key already used by a different command")
            job_id = digest((account_id, key))
            conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", (job_id, account_id, key, encoded(request),
                         digest(request), utc(self.clock.now()).isoformat()))
            return job_id

    def existing_job(self, account_id, key, request):
        with self.connection() as conn:
            row = conn.execute("SELECT id,request_hash FROM jobs WHERE account_id=? AND key=?", (account_id, key)).fetchone()
            if row and row["request_hash"] != digest(request):
                raise PaperConflict("Job key reused with different content")
            return row["id"] if row else None

    def job(self, job_id, conn=None):
        if conn is None:
            with self.connection() as handle:
                return self.job(job_id, handle)
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise PaperNotFound("Unknown replay job")
        if digest(json.loads(row["request"])) != row["request_hash"] or digest((row["account_id"],row["key"])) != job_id:
            raise PaperConflict("Accepted job content hash mismatch")
        result = conn.execute("SELECT result FROM commands WHERE account_id=? AND key=?", (row["account_id"], row["key"])).fetchone()
        error = conn.execute("SELECT code FROM job_errors WHERE job_id=?", (job_id,)).fetchone()
        return {"id": job_id, "account_id": row["account_id"], "key": row["key"],
                "request": json.loads(row["request"]), "status": "DONE" if result else "FAILED" if error else "QUEUED",
                "result": json.loads(result[0]) if result else None, "error": error[0] if error else None}

    def next_job(self, management_only=False):
        if not self.path.exists():
            return None
        with self.connection() as conn:
            row = conn.execute("""SELECT j.id FROM jobs j LEFT JOIN commands c ON c.account_id=j.account_id AND c.key=j.key
                LEFT JOIN job_errors e ON e.job_id=j.id WHERE c.key IS NULL AND e.job_id IS NULL
                AND (?=0 OR json_extract(j.request,'$.management_only')=1) ORDER BY j.created_at,j.id LIMIT 1""", (int(management_only),)).fetchone()
            return self.job(row[0], conn) if row else None

    def record_job_error(self, job_id, code):
        with self.connection(write=True) as conn:
            conn.execute("INSERT OR IGNORE INTO job_errors VALUES(?,?,?)", (job_id, code, utc(self.clock.now()).isoformat()))

    def backup(self, destination: Path):
        if destination.exists() or destination.resolve() == self.path.resolve():
            raise PaperConflict("Backup destination must be a new file")
        with closing(sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(destination)) as target:
                source.backup(target)

    def ambiguity_sensitivity(self, account_id):
        with self.connection() as conn:
            self._state(conn, account_id)
            count, total = 0, Decimal(0)
            for row in conn.execute("SELECT payload FROM events WHERE account_id=? AND kind='AMBIGUOUS_EXIT'", (account_id,)):
                count += 1
                total += money(json.loads(row[0])["target_first_incremental_proceeds"])
        return {"ambiguous_fills": count, "target_first_incremental_proceeds": str(money(total)),
                "scope": "Isolated fill sensitivity, not a reallocated alternative portfolio"}

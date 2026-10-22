"""S5 durable controls and lifecycle outbox; no financial state is stored here."""
from contextlib import contextmanager, closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3

import httpx
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.swing.ledger import PaperConflict, encoded
from app.swing.models import utc
from app.swing.setups import digest


class OperationsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    SWING_OPS_DB_PATH: str = "data/swing_ops.db"
    SWING_LIFECYCLE_WEBHOOK_URL: SecretStr = SecretStr("")
    SWING_LIFECYCLE_TIMEOUT_SEC: float = Field(5, gt=0, le=30, allow_inf_nan=False)
    SWING_LIFECYCLE_BATCH_SIZE: int = Field(20, ge=1, le=100)
    SWING_CHART_BARS: int = Field(120, ge=20, le=500)

    @model_validator(mode="after")
    def validate_settings(self):
        if not self.SWING_OPS_DB_PATH.strip():
            raise ValueError("Operations database path cannot be empty")
        url = self.SWING_LIFECYCLE_WEBHOOK_URL.get_secret_value()
        if url and not url.lower().startswith(("https://", "http://")):
            raise ValueError("Lifecycle destination must be an explicit HTTP(S) webhook")
        return self


DEFAULT = {"revision": 0, "paused": False, "alerts_paused": True, "selected_vintage": None, "alerts_since": None}
LIFECYCLES = {"PLAN_CAPTURED": "CANDIDATE", "ORDER_RESERVED": "ARMED", "ORDER_CANCELLED": "CANCELLED",
              "ORDER_EXPIRED": "EXPIRED", "BUY_FILLED": "OPEN", "SELL_FILLED": "EXIT_FILL", "DATA_UNRESOLVED": "UNRESOLVED"}


class SwingOperations:
    def __init__(self, settings, config, clock, validation_path="data/swing_validation.db"):
        self.settings, self.config, self.clock = settings, config, clock
        self.path = Path(settings.SWING_OPS_DB_PATH)
        protected = [Path(config.SWING_DB_PATH), Path(config.SWING_PAPER_DB_PATH), Path(validation_path),
                     Path("data/history.db"), Path(config.SWING_ARTIFACT_DIR)]
        if config.SWING_SOURCE_DB_PATH:
            protected.append(Path(config.SWING_SOURCE_DB_PATH))
        if self.path.resolve() in {p.resolve() for p in protected} or self.path.resolve().is_relative_to(Path(config.SWING_ARTIFACT_DIR).resolve()) or any(
                p.exists() and self.path.exists() and p.samefile(self.path) for p in protected if p.is_file()):
            raise ValueError("Operations store must be separate from source, observation, paper and validation evidence")

    @property
    def webhook_configured(self):
        return bool(self.settings.SWING_LIFECYCLE_WEBHOOK_URL.get_secret_value())

    @contextmanager
    def connection(self, write=False):
        if not write and not self.path.exists():
            raise FileNotFoundError(self.path)
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path.resolve().as_uri()+("?mode=rwc" if write else "?mode=ro"),
                                     uri=True, isolation_level=None, timeout=self.config.SQLITE_BUSY_TIMEOUT_SEC)) as conn:
            conn.row_factory = sqlite3.Row
            if not write:
                conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not tables and write:
                    for ddl in (
                        "CREATE TABLE ops_schema(version INTEGER PRIMARY KEY)", "INSERT INTO ops_schema VALUES(1)",
                        "CREATE TABLE controls(revision INTEGER PRIMARY KEY,created_at TEXT NOT NULL,payload TEXT NOT NULL,hash TEXT NOT NULL)",
                        "CREATE TABLE commands(key TEXT PRIMARY KEY,request_hash TEXT NOT NULL,result TEXT NOT NULL)",
                        "CREATE TABLE alerts(id TEXT PRIMARY KEY,payload TEXT NOT NULL,hash TEXT NOT NULL,destination TEXT NOT NULL)",
                        "CREATE TABLE cursors(account_id TEXT NOT NULL,seq INTEGER NOT NULL,PRIMARY KEY(account_id,seq))",
                        "CREATE TABLE claims(id TEXT NOT NULL,attempt INTEGER NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(id,attempt))",
                        "CREATE TABLE receipts(id TEXT NOT NULL,attempt INTEGER NOT NULL,status TEXT NOT NULL,PRIMARY KEY(id,attempt))",
                        "CREATE TABLE retries(id TEXT NOT NULL,attempt INTEGER NOT NULL,key TEXT UNIQUE NOT NULL,PRIMARY KEY(id,attempt))"):
                        conn.execute(ddl)
                    for table in ("ops_schema", "controls", "commands", "alerts", "cursors", "claims", "receipts", "retries"):
                        for action in ("UPDATE", "DELETE"):
                            conn.execute(f"CREATE TRIGGER immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Operations store is append-only'); END")
                elif "ops_schema" not in tables:
                    raise PaperConflict("Not an operations store; refusing migration")
                if tuple(conn.execute("SELECT max(version),count(*) FROM ops_schema").fetchone()) != (1, 1):
                    raise PaperConflict("Unsupported operations schema")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def controls(self, conn=None):
        if conn is None:
            if not self.path.exists():
                return dict(DEFAULT)
            with self.connection() as handle:
                return self.controls(handle)
        previous, state = "", dict(DEFAULT)
        for row in conn.execute("SELECT * FROM controls ORDER BY revision"):
            material = json.loads(row["payload"])
            if row["revision"] != state["revision"]+1 or material["previous_hash"] != previous or digest(material) != row["hash"]:
                raise PaperConflict("Broken operations revision/hash chain")
            state, previous = material["state"], row["hash"]
        return state

    def change(self, key, expected_revision, changes, validate=None):
        request = {"expected_revision": expected_revision, "changes": changes}
        with self.connection(True) as conn:
            old = conn.execute("SELECT request_hash,result FROM commands WHERE key=?", (key,)).fetchone()
            if old:
                if old["request_hash"] != digest(request):
                    raise PaperConflict("Control key reused with different content")
                return json.loads(old["result"])
            state = self.controls(conn)
            if state["revision"] != expected_revision:
                raise PaperConflict("Operations revision changed; reload before applying")
            if validate:
                validate(changes)
            restored = changes.get("rollback_revision")
            if restored is not None:
                row = conn.execute("SELECT payload FROM controls WHERE revision=?", (restored,)).fetchone()
                if restored != 0 and row is None:
                    raise PaperConflict("Unknown rollback revision")
                target = json.loads(row[0])["state"] if row else DEFAULT
                new = {**target, "paused": True, "alerts_paused": True}
            else:
                new = {**state, **changes}
            if not new["alerts_paused"] and not self.webhook_configured:
                raise PaperConflict("Configure a dedicated lifecycle destination before enabling alerts")
            if state["alerts_paused"] and not new["alerts_paused"]:
                new["alerts_since"] = utc(self.clock.now()).isoformat()
                # Timestamp equality cannot distinguish old/new events under an injected
                # clock. Snapshot existing heads at opt-in; never backfill old history.
                from app.swing.ledger import PaperLedger
                ledger = PaperLedger(Path(self.config.SWING_PAPER_DB_PATH), self.clock, self.config.SQLITE_BUSY_TIMEOUT_SEC)
                if ledger.path.exists():
                    with ledger.connection() as source:
                        for (account_id,) in source.execute("SELECT id FROM accounts"):
                            head = ledger._state(source, account_id)["version"]
                            conn.execute("INSERT OR IGNORE INTO cursors VALUES(?,?)", (account_id, head))
            new["revision"] = expected_revision+1
            previous = conn.execute("SELECT hash FROM controls ORDER BY revision DESC LIMIT 1").fetchone()
            material = {"state": new, "previous_hash": previous[0] if previous else "", "rollback_from": restored}
            conn.execute("INSERT INTO controls VALUES(?,?,?,?)", (new["revision"], utc(self.clock.now()).isoformat(), encoded(material), digest(material)))
            conn.execute("INSERT INTO commands VALUES(?,?,?)", (key, digest(request), encoded(new)))
            return new

    def history(self, limit=20):
        if not self.path.exists():
            return []
        with self.connection() as conn:
            self.controls(conn)
            return [{"revision": row["revision"], "created_at": row["created_at"], **json.loads(row["payload"])["state"]}
                    for row in conn.execute("SELECT * FROM controls ORDER BY revision DESC LIMIT ?", (limit,))]

    def require_new_work(self):
        if self.controls()["paused"]:
            raise PaperConflict("New swing work is paused; existing liabilities remain available")

    def collect(self, ledger):
        control = self.controls()
        if not self.webhook_configured or control["alerts_paused"] or not ledger.path.exists():
            return
        since = datetime.fromisoformat(control["alerts_since"])
        with ledger.connection() as source, self.connection(True) as conn:
            for (account_id,) in source.execute("SELECT id FROM accounts"):
                ledger._state(source, account_id)
                cursor = conn.execute("SELECT max(seq) FROM cursors WHERE account_id=?", (account_id,)).fetchone()[0] or 0
                from app.swing.ledger import empty_state, reduce_event
                reconstructed = empty_state(account_id)
                for event in source.execute("SELECT * FROM events WHERE account_id=? ORDER BY seq", (account_id,)):
                    p = json.loads(event["payload"])
                    reduce_event(reconstructed, event["kind"], p)
                    if event["seq"] <= cursor:
                        continue
                    if event["kind"] not in LIFECYCLES or datetime.fromisoformat(event["created_at"]) < since:
                        continue
                    event_id = digest((account_id, event["seq"], event["event_hash"]))
                    ticker = p.get("ticker")
                    if not ticker and p.get("plan_id"):
                        ticker = ledger.plan(account_id, p["plan_id"], source).ticker
                    lifecycle = reconstructed["orders"][p["plan_id"]]["lifecycle"] if event["kind"] == "SELL_FILLED" else LIFECYCLES[event["kind"]]
                    payload = {"event_id": event_id, "source_event_hash": event["event_hash"], "ticker": ticker,
                        "lifecycle": lifecycle, "event_type": event["kind"], "session": p.get("session"), "model_status": "RESEARCH_ONLY",
                        "notice": "Research simulation, not broker execution. Delayed data. Not financial advice."}
                    destination = digest(self.settings.SWING_LIFECYCLE_WEBHOOK_URL.get_secret_value())
                    conn.execute("INSERT OR IGNORE INTO alerts VALUES(?,?,?,?)", (event_id, encoded(payload), digest(payload), destination))
                head = source.execute("SELECT max(seq) FROM events WHERE account_id=?", (account_id,)).fetchone()[0]
                conn.execute("INSERT OR IGNORE INTO cursors VALUES(?,?)", (account_id, head))

    def outbox(self, limit=20):
        if not self.path.exists():
            return []
        with self.connection() as conn:
            result = []
            for row in conn.execute("SELECT * FROM alerts ORDER BY rowid DESC LIMIT ?", (limit,)):
                payload = json.loads(row["payload"])
                if digest(payload) != row["hash"]:
                    raise PaperConflict("Lifecycle outbox hash mismatch")
                claim = conn.execute("SELECT max(attempt) FROM claims WHERE id=?", (row["id"],)).fetchone()[0]
                receipt = conn.execute("SELECT status FROM receipts WHERE id=? AND attempt=?", (row["id"], claim)).fetchone()
                destination_matches = row["destination"] == digest(self.settings.SWING_LIFECYCLE_WEBHOOK_URL.get_secret_value())
                retry = conn.execute("SELECT max(attempt) FROM retries WHERE id=?", (row["id"],)).fetchone()[0] or 0
                status = "RETRY_PENDING" if retry > (claim or 0) else receipt[0] if receipt else "UNKNOWN" if claim else "PENDING"
                if not destination_matches and status not in {"SENT", "UNKNOWN", "REJECTED"}:
                    status = "DESTINATION_CHANGED"
                result.append({**payload, "attempt": claim or 0, "status": status, "destination_matches": destination_matches})
            return result

    def retry(self, event_id, key):
        with self.connection(True) as conn:
            destination = conn.execute("SELECT destination FROM alerts WHERE id=?", (event_id,)).fetchone()
            if destination is None or destination[0] != digest(self.settings.SWING_LIFECYCLE_WEBHOOK_URL.get_secret_value()):
                raise PaperConflict("Delivery destination changed; old events cannot be redirected")
            old = conn.execute("SELECT id,attempt FROM retries WHERE key=?", (key,)).fetchone()
            if old:
                if old["id"] != event_id:
                    raise PaperConflict("Retry key reused")
                return {"event_id": event_id, "attempt": old["attempt"]}
            claim = conn.execute("SELECT max(attempt) FROM claims WHERE id=?", (event_id,)).fetchone()[0]
            if not claim or conn.execute("SELECT 1 FROM receipts WHERE id=? AND status='SENT'", (event_id,)).fetchone():
                raise PaperConflict("Only uncertain/rejected deliveries can be explicitly retried")
            conn.execute("INSERT INTO retries VALUES(?,?,?)", (event_id, claim+1, key))
            return {"event_id": event_id, "attempt": claim+1}

    def dispatch(self, post=None):
        control = self.controls()
        if not self.webhook_configured or control["alerts_paused"] or not self.path.exists():
            return 0
        sent = 0
        for _ in range(self.settings.SWING_LIFECYCLE_BATCH_SIZE):
            with self.connection(True) as conn:
                item = conn.execute("""SELECT a.*,coalesce((SELECT max(attempt) FROM retries r WHERE r.id=a.id),1) attempt
                    FROM alerts a WHERE a.destination=? AND NOT EXISTS(SELECT 1 FROM claims c WHERE c.id=a.id AND c.attempt=
                    coalesce((SELECT max(attempt) FROM retries r WHERE r.id=a.id),1)) ORDER BY a.rowid LIMIT 1""",
                    (digest(self.settings.SWING_LIFECYCLE_WEBHOOK_URL.get_secret_value()),)).fetchone()
                if item is None:
                    break
                payload = json.loads(item["payload"])
                if digest(payload) != item["hash"]:
                    raise PaperConflict("Lifecycle outbox hash mismatch")
                conn.execute("INSERT INTO claims VALUES(?,?,?)", (item["id"], item["attempt"], utc(self.clock.now()).isoformat()))
            status = "UNKNOWN"
            try:
                response = (post or httpx.post)(self.settings.SWING_LIFECYCLE_WEBHOOK_URL.get_secret_value(),
                    json=payload, headers={"Idempotency-Key": item["id"]}, timeout=self.settings.SWING_LIFECYCLE_TIMEOUT_SEC,
                    follow_redirects=False)
                status = "SENT" if 200 <= response.status_code < 300 else "REJECTED"
            except Exception:
                pass  # URLs/response bodies can contain secrets; durable UNKNOWN is sufficient.
            with self.connection(True) as conn:
                conn.execute("INSERT INTO receipts VALUES(?,?,?)", (item["id"], item["attempt"], status))
            sent += status == "SENT"
        return sent

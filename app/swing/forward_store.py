"""S6 immutable campaign and daily evidence journal; no trading state mutations."""
from contextlib import contextmanager, closing
import json
from pathlib import Path
import sqlite3

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.swing.ledger import PaperConflict, PaperNotFound, encoded
from app.swing.models import utc
from app.swing.setups import digest


class ForwardSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    SWING_FORWARD_DB_PATH: str = Field("data/swing_forward.db", min_length=1)
    SWING_FORWARD_MIN_SESSIONS: int = Field(30, ge=30, le=5000)


class ForwardJournal:
    def __init__(self, settings, clock, protected=(), timeout=30):
        self.path, self.clock, self.timeout = Path(settings.SWING_FORWARD_DB_PATH), clock, timeout
        if not str(settings.SWING_FORWARD_DB_PATH).strip():
            raise ValueError("Forward journal path cannot be blank")
        for value in protected:
            other = Path(value)
            if self.path.resolve() == other.resolve() or (other.is_dir() and self.path.resolve().is_relative_to(other.resolve())) or (
                    self.path.exists() and other.is_file() and self.path.samefile(other)):
                raise ValueError("Forward journal must be separate from other stores and evidence")

    @contextmanager
    def connection(self, write=False):
        if not write and not self.path.exists():
            raise PaperNotFound("No forward campaigns registered")
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path.resolve().as_uri()+("?mode=rwc" if write else "?mode=ro"),
                                    uri=True, timeout=self.timeout, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            if not write:
                conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not tables and write:
                    for ddl in ("CREATE TABLE forward_schema(version INTEGER PRIMARY KEY)",
                                "INSERT INTO forward_schema VALUES(1)",
                                "CREATE TABLE campaigns(id TEXT PRIMARY KEY,account_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,hash TEXT NOT NULL)",
                                "CREATE TABLE days(campaign TEXT NOT NULL,session TEXT NOT NULL,seq INTEGER NOT NULL,payload TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(campaign,session),UNIQUE(campaign,seq))",
                                "CREATE TABLE receipts(campaign TEXT NOT NULL,key TEXT NOT NULL,request_hash TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(campaign,key))"):
                        conn.execute(ddl)
                    for table in ("forward_schema", "campaigns", "days", "receipts"):
                        for action in ("UPDATE", "DELETE"):
                            conn.execute(f"CREATE TRIGGER immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Forward journal is append-only'); END")
                elif "forward_schema" not in tables:
                    raise PaperConflict("Not a forward journal; refusing migration")
                if tuple(conn.execute("SELECT max(version),count(*) FROM forward_schema").fetchone()) != (1, 1):
                    raise PaperConflict("Unsupported forward schema")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def read(self, campaign, conn=None):
        if conn is None:
            with self.connection() as handle:
                return self.read(campaign, handle)
        row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign,)).fetchone()
        if row is None:
            raise PaperNotFound("Unknown forward campaign")
        initial = json.loads(row["payload"])
        previous = digest(initial)
        if previous != row["hash"]:
            raise PaperConflict("Campaign content hash mismatch")
        days = []
        for index, record in enumerate(conn.execute("SELECT * FROM days WHERE campaign=? ORDER BY seq", (campaign,)), 1):
            payload = json.loads(record["payload"])
            current = digest((previous, index, payload))
            if record["seq"] != index or current != record["hash"] or payload["session"] != record["session"]:
                raise PaperConflict("Forward evidence chain mismatch")
            days.append({"hash": current, "payload": payload})
            previous = current
        return {"registration": initial, "days": days, "head": previous}

    def receipt(self, campaign, key, request, conn=None):
        if not self.path.exists():
            return None
        if conn is None:
            with self.connection() as handle:
                return self.receipt(campaign, key, request, handle)
        row = conn.execute("SELECT * FROM receipts WHERE campaign=? AND key=?", (campaign, key)).fetchone()
        if row:
            if row["request_hash"] != digest(request):
                raise PaperConflict("Forward command key reused with different content")
            result = json.loads(row["result"])
            material = self.read(campaign, conn)  # verify evidence even on a retry
            if result["hash"] not in {material["head"], digest(material["registration"]), *[d["hash"] for d in material["days"]]}:
                raise PaperConflict("Forward receipt is not linked to evidence")
            return result
        return None

    def register(self, campaign, account_id, key, request, registration):
        with self.connection(True) as conn:
            old = self.receipt(campaign, key, request, conn)
            if old:
                return old
            value = {**registration, "registered_at": utc(self.clock.now()).isoformat()}
            result = {"campaign": campaign, "hash": digest(value)}
            try:
                conn.execute("INSERT INTO campaigns VALUES(?,?,?,?)", (campaign, account_id, encoded(value), result["hash"]))
            except sqlite3.IntegrityError as exc:
                raise PaperConflict("Campaign/account already registered") from exc
            conn.execute("INSERT INTO receipts VALUES(?,?,?,?)", (campaign, key, digest(request), encoded(result)))
            return result

    def append(self, campaign, key, request, expected_head, payload):
        with self.connection(True) as conn:
            old = self.receipt(campaign, key, request, conn)
            if old:
                return old
            prior = self.read(campaign, conn)
            if prior["head"] != expected_head:
                raise PaperConflict("Forward journal changed; review and retry")
            if prior["days"] and payload["session"] <= prior["days"][-1]["payload"]["session"]:
                raise PaperConflict("Session already sealed or would backfill history")
            seq = len(prior["days"])+1
            result = {"campaign": campaign, "session": payload["session"], "hash": digest((expected_head, seq, payload))}
            conn.execute("INSERT INTO days VALUES(?,?,?,?,?)", (campaign, payload["session"], seq, encoded(payload), result["hash"]))
            conn.execute("INSERT INTO receipts VALUES(?,?,?,?)", (campaign, key, digest(request), encoded(result)))
            return result

    def campaigns(self):
        if not self.path.exists():
            return []
        with self.connection() as conn:
            return [self.read(row[0], conn) for row in conn.execute("SELECT id FROM campaigns ORDER BY id")]

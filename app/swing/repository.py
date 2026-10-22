"""Separate, transactional, append-only swing observations and source snapshots."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import json
import sqlite3

from app.providers.swing_archive import file_hash
from app.swing.models import ContextRequest, DailyPrice, DatedContext, HistoryRequest, HistoryResult, utc

SCHEMA_VERSION = 1
DDL = (
    "CREATE TABLE swing_schema(version INTEGER PRIMARY KEY)",
    """CREATE TABLE snapshots(id TEXT PRIMARY KEY,source_uri TEXT NOT NULL,observed_at TEXT NOT NULL,
        imported_at TEXT NOT NULL,artifact_path TEXT NOT NULL,header TEXT NOT NULL)""",
    "CREATE TABLE reports(snapshot_id TEXT PRIMARY KEY REFERENCES snapshots(id),report TEXT NOT NULL)",
    """CREATE TABLE prices(snapshot_id TEXT NOT NULL REFERENCES snapshots(id),ticker TEXT NOT NULL,
        session TEXT NOT NULL,basis TEXT NOT NULL,payload TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,ticker,session))""",
    """CREATE TABLE contexts(snapshot_id TEXT NOT NULL REFERENCES snapshots(id),kind TEXT NOT NULL,
        ticker TEXT NOT NULL,session TEXT NOT NULL,payload TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,kind,ticker,session))""",
    """CREATE TABLE rejects(snapshot_id TEXT NOT NULL REFERENCES snapshots(id),line INTEGER NOT NULL,
        ticker TEXT,session TEXT,code TEXT NOT NULL,PRIMARY KEY(snapshot_id,line))""",
)


class SwingRepository:
    def __init__(self, path: Path, timeout: float = 30, calendar=None):
        self.path, self.timeout = Path(path), timeout
        self.calendar = calendar

    @contextmanager
    def connection(self, *, write: bool = False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        elif not self.path.exists():
            raise FileNotFoundError(self.path)
        uri = self.path.resolve().as_uri() + ("?mode=rwc" if write else "?mode=ro")
        conn = sqlite3.connect(uri, uri=True, timeout=self.timeout, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            if not write:
                conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='swing_schema'").fetchone()
            if not exists:
                if not write:
                    raise ValueError("Uninitialized swing repository")
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    raise ValueError("Refusing to migrate an unrelated database")
                for statement in DDL:
                    conn.execute(statement)
                conn.execute("INSERT INTO swing_schema VALUES(?)", (SCHEMA_VERSION,))
                for table in ("snapshots", "reports", "prices", "contexts", "rejects"):
                    for action in ("UPDATE", "DELETE"):
                        conn.execute(f"CREATE TRIGGER immutable_{table}_{action} BEFORE {action} ON {table} "
                                     "BEGIN SELECT RAISE(ABORT,'Swing evidence is immutable'); END")
                for table in ("prices", "contexts", "rejects"):
                    conn.execute(f"CREATE TRIGGER sealed_{table}_INSERT BEFORE INSERT ON {table} "
                                 "WHEN EXISTS(SELECT 1 FROM reports WHERE snapshot_id=NEW.snapshot_id) "
                                 "BEGIN SELECT RAISE(ABORT,'Swing evidence is immutable'); END")
            version = conn.execute("SELECT max(version) FROM swing_schema").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise ValueError("Unsupported swing schema version")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def report(self, vintage: str) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT report FROM reports WHERE snapshot_id=?", (vintage,)).fetchone()
            if row is None:
                raise ValueError("Unknown snapshot")
            return json.loads(row[0])

    def verify(self, vintage: str) -> dict:
        """Explicit evidence verification, without opening or changing the source project."""
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM snapshots WHERE id=?", (vintage,)).fetchone()
            if row is None:
                raise ValueError("Unknown snapshot")
            artifact = Path(row["artifact_path"])
            if file_hash(artifact) != vintage:
                raise ValueError("Snapshot artifact hash mismatch")
            header = json.loads(row["header"])
            universe = artifact.parent.parent / f"universe-{header['universe_sha256']}.csv"
            if file_hash(universe) != header["universe_sha256"]:
                raise ValueError("Universe artifact hash mismatch")
            if header.get("reuse_manifest_sha256"):
                reuse = artifact.parent.parent / f"reuse-{header['reuse_manifest_sha256']}.json"
                if file_hash(reuse) != header["reuse_manifest_sha256"]:
                    raise ValueError("Reuse manifest hash mismatch")
            return {"snapshot_id": vintage, "artifact_verified": True, "universe_verified": True}

    @staticmethod
    def _visible(conn, vintage: str, as_of: datetime):
        row = conn.execute("SELECT * FROM snapshots WHERE id=?", (vintage,)).fetchone()
        return row if row and datetime.fromisoformat(row["imported_at"]) <= utc(as_of) else None

    def history(self, request: HistoryRequest) -> HistoryResult:
        with self.connection() as conn:
            snapshot = self._visible(conn, request.vintage, request.as_of)
            if snapshot is None:
                return HistoryResult("unavailable", "vintage_not_available_at_decision")
            rows = conn.execute("""SELECT basis,payload FROM prices WHERE snapshot_id=? AND ticker=?
                AND session>=? AND session<=? ORDER BY session""",
                (request.vintage, request.ticker, request.start.isoformat(), request.end.isoformat())).fetchall()
            if not rows:
                return HistoryResult("unavailable", "no_exact_vintage_prices")
            bases = {row["basis"] for row in rows}
            if bases != {request.required_basis}:
                return HistoryResult("conflicting" if len(bases) > 1 else "unavailable", "price_basis_unresolved_or_mismatched")
            from app.market.calendar import MarketCalendar
            calendar = self.calendar or MarketCalendar("NSE")
            expected = {d.date().isoformat() for d in calendar.calendar.sessions_in_range(request.start, request.end)}
            observed = {json.loads(row["payload"])["session"] for row in rows}
            if observed != expected:
                return HistoryResult("unavailable", "missing_exact_sessions")
            return HistoryResult("available", None, tuple(DailyPrice.model_validate_json(row["payload"]) for row in rows))

    def context(self, request: ContextRequest) -> DatedContext:
        with self.connection() as conn:
            snapshot = self._visible(conn, request.vintage, request.as_of)
            row = conn.execute("""SELECT payload FROM contexts WHERE snapshot_id=? AND ticker=? AND kind=?
                AND session=?""", (request.vintage, request.ticker, request.kind, request.session.isoformat())).fetchone() if snapshot else None
            if row:
                context = DatedContext.model_validate_json(row["payload"])
                if context.available_at <= utc(request.as_of):
                    return context
        return DatedContext(kind=request.kind, ticker=request.ticker, session=request.session,
                            source="unavailable", ingested_at=utc(request.as_of), status="unavailable")

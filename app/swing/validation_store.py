"""Immutable preregistrations, one-shot holdouts and authenticated manifests."""
from contextlib import contextmanager, closing
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3

from app.swing.ledger import encoded
from app.swing.models import utc
from app.swing.setups import digest, implementation_hash
from app.swing.validation_protocol import Study, code_hashes, runtime_fingerprint, schedule


class ValidationRegistry:
    def __init__(self, settings, clock):
        self.path = Path(settings.VALIDATION_DB_PATH)
        self.key = settings.VALIDATION_SIGNING_KEY.get_secret_value().encode()
        self.clock = clock

    def envelope(self, payload):
        return {"payload": payload, "sha256": digest(payload), "authentication": "HMAC-SHA256" if self.key else "UNSIGNED",
                "key_id": hashlib.sha256(self.key).hexdigest() if self.key else None,
                "signature": hmac.new(self.key, encoded(payload).encode(), hashlib.sha256).hexdigest() if self.key else None}

    def verify(self, record):
        if record["sha256"] != digest(record["payload"]):
            raise ValueError("Manifest hash mismatch")
        if record["authentication"] == "HMAC-SHA256":
            if not self.key or record["key_id"] != hashlib.sha256(self.key).hexdigest() or not hmac.compare_digest(
                    record["signature"], self.envelope(record["payload"])["signature"]):
                raise ValueError("Manifest authentication failed; original dedicated key required")
        elif record["authentication"] != "UNSIGNED" or record["signature"] is not None or record["key_id"] is not None:
            raise ValueError("Unknown or inconsistent manifest authentication")
        return record["payload"]

    @contextmanager
    def connection(self, write=False):
        if not write and not self.path.exists():
            raise FileNotFoundError(self.path)
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path.resolve().as_uri()+("?mode=rwc" if write else "?mode=ro"),
                                     uri=True, isolation_level=None, timeout=30)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            if not write:
                conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not tables and write:
                    for ddl in (
                        "CREATE TABLE validation_schema(version INTEGER PRIMARY KEY)",
                        "INSERT INTO validation_schema VALUES(1)",
                        "CREATE TABLE studies(id TEXT PRIMARY KEY, record TEXT NOT NULL)",
                        "CREATE TABLE claims(study_id TEXT PRIMARY KEY REFERENCES studies(id), opened_at TEXT NOT NULL)",
                        "CREATE TABLE reports(id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES studies(id), phase TEXT NOT NULL, record TEXT NOT NULL)"):
                        conn.execute(ddl)
                    for table in ("validation_schema", "studies", "claims", "reports"):
                        for operation in ("UPDATE", "DELETE"):
                            conn.execute(f"CREATE TRIGGER immutable_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Validation registry is append-only'); END")
                elif "validation_schema" not in tables:
                    raise ValueError("Not a validation registry; refusing migration")
                if conn.execute("SELECT max(version),count(*) FROM validation_schema").fetchone() != (1, 1):
                    raise ValueError("Unsupported validation registry schema")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def register(self, study, calendar):
        frozen = {"protocol": study.model_dump(mode="json"), "schedule": schedule(study, calendar),
                  "code_hashes": code_hashes(), "setup_implementation": implementation_hash(),
                  "runtime": runtime_fingerprint(),
                  "registered_at": utc(self.clock.now()).isoformat()}
        with self.connection(write=True) as conn:
            old = conn.execute("SELECT record FROM studies WHERE id=?", (study.id,)).fetchone()
            if old:
                record = json.loads(old[0])
                if self.verify(record)["protocol"] != frozen["protocol"]:
                    raise ValueError("Study ID already freezes a different protocol")
                return record
            if calendar.calendar.session_open(str(study.holdout.start)).to_pydatetime() <= utc(self.clock.now()):
                raise ValueError("Terminal holdout must begin strictly after preregistration")
            if any(calendar.calendar.session_open(str(f.test.start)).to_pydatetime() <= utc(self.clock.now()) for f in study.folds):
                raise ValueError("Every out-of-sample test must begin after preregistration")
            for (raw,) in conn.execute("SELECT record FROM studies"):
                prior = Study.model_validate(self.verify(json.loads(raw))["protocol"])
                if set(prior.strategies) & set(study.strategies) and max(prior.holdout.start, study.holdout.start) <= min(prior.holdout.end, study.holdout.end):
                    raise ValueError("Holdout dates already reserved for this strategy; reuse forbidden")
            record = self.envelope(frozen)
            conn.execute("INSERT INTO studies VALUES(?,?)", (study.id, encoded(record)))
        return record

    def study(self, study_id):
        with self.connection() as conn:
            row = conn.execute("SELECT record FROM studies WHERE id=?", (study_id,)).fetchone()
        if not row:
            raise ValueError("Unknown study")
        record = json.loads(row[0])
        self.verify(record)
        return record

    def claim_holdout(self, study_id, calendar):
        record = self.study(study_id)
        study = Study.model_validate(record["payload"]["protocol"])
        if calendar.calendar.session_close(str(study.holdout.end)).to_pydatetime() >= utc(self.clock.now()):
            raise ValueError("Entire terminal holdout must complete before opening")
        with self.connection(write=True) as conn:
            if conn.execute("SELECT 1 FROM claims WHERE study_id=?", (study_id,)).fetchone():
                raise ValueError("Holdout already consumed; no retry or retuning permitted")
            conn.execute("INSERT INTO claims VALUES(?,?)", (study_id, utc(self.clock.now()).isoformat()))

    def save(self, study_id, phase, payload):
        record = self.envelope(payload)
        with self.connection(write=True) as conn:
            conn.execute("INSERT OR IGNORE INTO reports VALUES(?,?,?,?)", (record["sha256"], study_id, phase, encoded(record)))
        return record

    def report(self, report_id):
        with self.connection() as conn:
            row = conn.execute("SELECT record FROM reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise ValueError("Unknown report")
        record = json.loads(row[0])
        self.verify(record)
        return record

    def holdout_report(self, study_id):
        with self.connection() as conn:
            row = conn.execute("SELECT id FROM reports WHERE study_id=? AND phase='holdout'", (study_id,)).fetchone()
        return self.report(row[0]) if row else None

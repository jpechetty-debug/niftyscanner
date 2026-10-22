"""Read-only export of allowlisted public caches; never import a sibling application."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.core.interfaces import Clock
from app.market.calendar import MarketCalendar
from app.swing.models import utc
from app.universe.nse import NSEUniverse

ADAPTER_VERSION = "multibagger-public-cache-v1"
TABLES = {
    "prices": ("symbol", "date", "open", "high", "low", "close", "volume"),
    "delivery_data": ("symbol", "date", "volume", "delivery_qty", "delivery_pct", "source"),
    "benchmarks": ("symbol", "date", "close"),
}


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _evidence_value(value):
    # Preserve invalid SQLite numeric types in evidence instead of producing invalid JSON.
    if isinstance(value, float) and not math.isfinite(value):
        return {"sqlite_nonfinite": repr(value)}
    return value


def last_completed(calendar: MarketCalendar, now: datetime) -> str:
    instant = pd.Timestamp(utc(now))
    session = calendar.calendar.date_to_session(now.astimezone(calendar.tz).date(), direction="previous")
    if calendar.calendar.session_close(session) > instant:
        session = calendar.calendar.previous_session(session)
    return session.date().isoformat()


@dataclass(frozen=True)
class Selection:
    sha256: str
    original: bytes
    members: dict[str, dict]


def load_selection(path: Path) -> Selection:
    """Use the existing real-universe exclusions; metadata describes current membership only."""
    original = path.read_bytes()  # Missing universe must fail; no invented fallback.
    eligible = {row.ticker for row in NSEUniverse(path).load()}
    members = {}
    frame = pd.read_csv(io.BytesIO(original), comment="#", dtype=str).fillna("")
    for raw in frame.to_dict("records"):
        row = {str(k).strip().lower(): (str(v).strip() if v is not None else "") for k, v in raw.items()}
        ticker = row.get("symbol", "") + ".NS"
        if ticker in eligible:
            metadata = {"current_series": row.get("series") or None, "current_isin": row.get("isin code") or None}
            if ticker in members and metadata != members[ticker]:
                raise ValueError(f"Conflicting current universe identity: {ticker}")
            members[ticker] = metadata
    if not members:
        raise ValueError("No eligible symbols in supplied universe")
    if path.read_bytes() != original:
        raise ValueError("Universe changed during selection; retry from a stable file")
    return Selection(hashlib.sha256(original).hexdigest(), original, dict(sorted(members.items())))


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    path: Path
    source_uri: str
    observed_at: datetime
    header: dict
    rows: int


def export_snapshot(source: Path, artifacts: Path, selection: Selection, calendar: MarketCalendar,
                    clock: Clock, benchmark: str, timeout: float, history_sessions: int,
                    delivery_sessions: int) -> Snapshot:
    """One consistent SQLite read transaction; deterministic content-addressed selected export."""
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("Source must be an existing SQLite file")
    observed = utc(clock.now())
    completed = last_completed(calendar, observed)
    sessions = calendar.calendar.sessions
    preceding = sessions[sessions <= pd.Timestamp(completed)]
    if min(history_sessions, delivery_sessions) < 1 or max(history_sessions, delivery_sessions) > len(preceding):
        raise ValueError("Insufficient calendar coverage for requested windows")
    price_start = preceding[-history_sessions].date().isoformat()
    delivery_start = preceding[-delivery_sessions].date().isoformat()
    root = artifacts.resolve()
    snapshots = root / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    uri = source.as_uri() + "?mode=ro"
    handle_fd, temporary = tempfile.mkstemp(prefix="export-", suffix=".jsonl", dir=snapshots)
    temp = Path(temporary)
    try:
        with os.fdopen(handle_fd, "wb") as output, closing(sqlite3.connect(uri, uri=True, timeout=timeout)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            table_names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "prices" not in table_names:
                raise ValueError("Source has no supported prices table")
            schemas = {}
            for table, columns in TABLES.items():
                if table in table_names:
                    schema = [dict(row) for row in conn.execute(f"PRAGMA table_info({table})")]
                    if set(columns) - {row["name"] for row in schema}:
                        raise ValueError(f"Unsupported {table} schema")
                    schemas[table] = schema
            header = {"format": 1, "adapter": ADAPTER_VERSION, "source_uri": source.as_uri(),
                      "universe_sha256": selection.sha256, "membership_scope": "current_supplied_universe",
                      "members": selection.members, "benchmark": benchmark, "schemas": schemas,
                      "last_completed_session": completed,
                      "price_window_start": price_start, "delivery_window_start": delivery_start,
                      "calendar_policy": {"market": calendar.market,
                        "extra_holidays": getattr(calendar.calendar, "extra_holidays", pd.DatetimeIndex([])).strftime("%Y-%m-%d").tolist()},
                      "price_basis": "unknown", "publication_verified": False,
                      "historical_identity_verified": False}
            implementation = Path(__file__).resolve().parents[1] / "swing"
            header["validation_policy"] = {"version": "swing-cache-validation-v1",
                "adapter_sha256": file_hash(Path(__file__)),
                "data_sha256": file_hash(implementation / "data.py"),
                "models_sha256": file_hash(implementation / "models.py")}
            reuse_file = Path(__file__).resolve().parents[2] / "docs" / "swing" / "SOURCE_MANIFEST.json"
            header["reuse_manifest_sha256"] = file_hash(reuse_file) if reuse_file.exists() else None
            header["source_benchmarks"] = [row[0] for row in conn.execute("SELECT DISTINCT symbol FROM benchmarks ORDER BY symbol")] if "benchmarks" in schemas else []
            digest, count = hashlib.sha256(), 0

            def emit(value):
                block = (canonical(value) + "\n").encode("utf-8")
                digest.update(block)
                output.write(block)

            emit({"header": header})
            for table, columns in TABLES.items():
                if table not in schemas:
                    continue
                symbols = [benchmark] if table == "benchmarks" else sorted({*selection.members, benchmark} if table == "prices" else selection.members)
                for ticker in symbols:
                    start = delivery_start if table == "delivery_data" else price_start
                    query = f"SELECT {','.join(columns)} FROM {table} WHERE symbol=? AND (date>=? OR date IS NULL OR length(date)!=10) ORDER BY date"
                    for row in conn.execute(query, (ticker, start)):
                        emit({"table": table, "row": {key: _evidence_value(row[key]) for key in columns}})
                        count += 1
            output.flush()
            os.fsync(output.fileno())
            snapshot_id = digest.hexdigest()
        destination = snapshots / f"{snapshot_id}.jsonl"
        if destination.exists():
            if file_hash(destination) != snapshot_id:
                raise ValueError("Existing snapshot evidence was modified")
            temp.unlink()
        else:
            os.replace(temp, destination)
        if header["reuse_manifest_sha256"]:
            reuse_evidence = root / f"reuse-{header['reuse_manifest_sha256']}.json"
            if reuse_evidence.exists():
                if file_hash(reuse_evidence) != header["reuse_manifest_sha256"]:
                    raise ValueError("Frozen reuse manifest was modified")
            else:
                with reuse_evidence.open("xb") as handle:
                    body = reuse_file.read_bytes()
                    if hashlib.sha256(body).hexdigest() != header["reuse_manifest_sha256"]:
                        raise ValueError("Reuse manifest changed while exporting")
                    handle.write(body)
        universe_file = root / f"universe-{selection.sha256}.csv"
        if universe_file.exists():
            if file_hash(universe_file) != selection.sha256:
                raise ValueError("Frozen universe evidence was modified")
        else:
            # Exclusive creation avoids replacing evidence held by another importer.
            try:
                with universe_file.open("xb") as handle:
                    handle.write(selection.original)
            except FileExistsError:
                if file_hash(universe_file) != selection.sha256:
                    raise ValueError("Conflicting frozen universe evidence")
        return Snapshot(snapshot_id, destination, source.as_uri(), observed, header, count)
    finally:
        if temp.exists():
            temp.unlink()

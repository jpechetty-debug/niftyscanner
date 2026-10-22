"""Canonicalize frozen public-cache observations, retaining honest unavailable states."""
from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

import pandas as pd
from pydantic import ValidationError

from app.core.config import Settings
from app.core.interfaces import Clock
from app.market.calendar import MarketCalendar
from app.providers.swing_archive import Snapshot, canonical, file_hash
from app.swing.models import DailyPrice, DatedContext, utc
from app.swing.repository import SwingRepository


def _day(value) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Expected canonical ISO session date")
    return parsed


def _number(value) -> float:
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Expected finite numeric source value")
    return float(value)


class RejectedRow(ValueError):
    pass


def _session(raw, snapshot: Snapshot, calendar: MarketCalendar) -> date:
    try:
        session = _day(raw["date"])
    except (ValueError, TypeError, KeyError):
        raise RejectedRow("INVALID_SESSION") from None
    if session.isoformat() > snapshot.header["last_completed_session"]:
        raise RejectedRow("UNCOMPLETED_OR_FUTURE_SESSION")
    if not calendar.calendar.is_session(session):
        raise RejectedRow("NON_SESSION")
    return session


def _observation(table: str, raw: dict, snapshot: Snapshot, calendar: MarketCalendar,
                 imported_at) -> DailyPrice | DatedContext:
    session = _session(raw, snapshot, calendar)
    ticker = raw["symbol"]
    if table == "delivery_data":
        volume, quantity, pct = (_number(raw[name]) for name in ("volume", "delivery_qty", "delivery_pct"))
        if volume <= 0 or not 0 <= quantity <= volume or not 0 <= pct <= 100:
            raise RejectedRow("INVALID_DELIVERY")
        # Exchange percentage is rounded to 0.01 pp: half a reporting unit plus float representation tolerance.
        difference = abs(Decimal(str(pct)) - 100 * Decimal(str(quantity)) / Decimal(str(volume)))
        if difference > Decimal("0.005000001"):
            raise RejectedRow("CONTRADICTORY_DELIVERY_PERCENTAGE")
        return DatedContext(kind="delivery", ticker=ticker, session=session,
            source=str(raw.get("source") or "unknown_source"), ingested_at=imported_at,
            status="available", values={"volume": volume, "delivery_qty": quantity, "delivery_pct": pct,
                "historical_identity_verified": False, "source_finality_verified": False})
    instrument = "index" if ticker == snapshot.header["benchmark"] else "equity"
    values = {name: _number(raw[name]) if raw.get(name) is not None else None
              for name in ("open", "high", "low", "close", "volume")}
    return DailyPrice(ticker=ticker, session=session, instrument=instrument,
                      price_basis="unknown", **values)


def coverage(conn, snapshot: Snapshot, config: Settings, rejected: Counter) -> dict:
    """Exact expected-session coverage, not an inference from the number of rows."""
    header = snapshot.header
    calendar = MarketCalendar("NSE", config=config)
    completed = header["last_completed_session"]
    all_sessions = calendar.calendar.sessions
    preceding = all_sessions[all_sessions <= pd.Timestamp(completed)]
    needed_prices = [day.date().isoformat() for day in preceding[-config.SWING_MIN_HISTORY_SESSIONS:]]
    needed_delivery = [day.date().isoformat() for day in preceding[-config.SWING_DELIVERY_SESSIONS:]]
    members = []
    for ticker, identity in header["members"].items():
        prices = [row[0] for row in conn.execute("SELECT session FROM prices WHERE snapshot_id=? AND ticker=? ORDER BY session",
                                                (snapshot.snapshot_id, ticker))]
        delivery = [row[0] for row in conn.execute("SELECT session FROM contexts WHERE snapshot_id=? AND ticker=? AND kind='delivery' ORDER BY session",
                                                  (snapshot.snapshot_id, ticker))]
        price_set, delivery_set = set(prices), set(delivery)
        missing_prices = [day for day in needed_prices if day not in price_set]
        missing_delivery = [day for day in needed_delivery if day not in delivery_set]
        members.append({"ticker": ticker, **identity, "historical_identity_verified": False,
            "price_rows": len(prices), "latest_price": prices[-1] if prices else None,
            "missing_price_sessions": missing_prices, "price_basis": "unknown",
            "delivery_rows": len(delivery), "latest_delivery": delivery[-1] if delivery else None,
            "missing_delivery_sessions": missing_delivery,
            "price_status": "missing" if not prices else "basis_unverified",
            "delivery_status": "unavailable" if not delivery else "stale" if completed not in delivery_set else "available",
            "eligible_for_swing": False})
    benchmark = [dict(row) for row in conn.execute("SELECT session,payload FROM prices WHERE snapshot_id=? AND ticker=? ORDER BY session",
                                                  (snapshot.snapshot_id, config.SWING_BENCHMARK))]
    benchmark_sessions = {row["session"] for row in benchmark}
    benchmark_opens = sum(json.loads(row["payload"])["open"] is not None for row in benchmark)
    return {"snapshot_id": snapshot.snapshot_id, "adapter": header["adapter"], "source_uri": snapshot.source_uri,
        "universe_sha256": header["universe_sha256"], "membership_scope": header["membership_scope"],
        "last_completed_session": completed, "required_price_sessions": len(needed_prices),
        "required_delivery_sessions": len(needed_delivery), "source_rows": snapshot.rows,
        "accepted_prices": conn.execute("SELECT count(*) FROM prices WHERE snapshot_id=?", (snapshot.snapshot_id,)).fetchone()[0],
        "accepted_contexts": conn.execute("SELECT count(*) FROM contexts WHERE snapshot_id=?", (snapshot.snapshot_id,)).fetchone()[0],
        "rejected": dict(sorted(rejected.items())), "members": members,
        "summary": {"universe": len(members), "symbols_with_prices": sum(m["price_rows"] > 0 for m in members),
            "complete_trailing_price_histories": sum(not m["missing_price_sessions"] for m in members),
            "complete_trailing_delivery_histories": sum(not m["missing_delivery_sessions"] for m in members),
            "symbols_without_delivery": sum(not m["delivery_rows"] for m in members), "swing_eligible": 0},
        "benchmark": {"ticker": config.SWING_BENCHMARK, "source_inventory": header["source_benchmarks"],
            "rows": len(benchmark), "open_rows": benchmark_opens,
            "missing_sessions": [day for day in needed_prices if day not in benchmark_sessions],
            "status": "unavailable" if not benchmark else "basis_unverified"},
        "limitations": ["Price adjustment and historical identity are unverified in public cache rows",
            "Local import time is availability; historical session dates are not publication timestamps",
            "Current constituent selection does not establish historical Nifty 500 membership",
            "Source finality is unverified; calendar completion alone is not provider confirmation",
            "No sector, surveillance, earnings or independent financial context imported in S1",
            "No data here is promoted to trade eligibility"]}


def import_snapshot(snapshot: Snapshot, repository: SwingRepository, calendar: MarketCalendar,
                    config: Settings, clock: Clock) -> dict:
    """Commit a verified artifact and its canonical/rejected rows as one transaction."""
    if snapshot.path.resolve() == repository.path.resolve():
        raise ValueError("Snapshot and repository paths must differ")
    if file_hash(snapshot.path) != snapshot.snapshot_id:
        raise ValueError("Snapshot artifact hash mismatch")
    imported_at = utc(clock.now())
    if imported_at < snapshot.observed_at:
        raise ValueError("Import clock precedes source observation")
    with repository.connection(write=True) as conn:
        existing = conn.execute("SELECT report FROM reports WHERE snapshot_id=?", (snapshot.snapshot_id,)).fetchone()
        if existing:
            return {**json.loads(existing[0]), "reused": True}
        # Children and manifest commit together. Defer FKs so availability can be stamped at finalization.
        conn.execute("PRAGMA defer_foreign_keys=ON")
        rejected, prices, contexts, failures = Counter(), [], [], []
        conn.execute("CREATE TEMP TABLE import_identities(kind TEXT,ticker TEXT,session TEXT,PRIMARY KEY(kind,ticker,session)) WITHOUT ROWID")
        allowed_prices = {*snapshot.header["members"], snapshot.header["benchmark"]}
        allowed_delivery = set(snapshot.header["members"])

        def flush():
            conn.executemany("INSERT INTO prices VALUES(?,?,?,?,?)", prices)
            conn.executemany("INSERT INTO contexts VALUES(?,?,?,?,?)", contexts)
            conn.executemany("INSERT INTO rejects VALUES(?,?,?,?,?)", failures)
            prices.clear(); contexts.clear(); failures.clear()

        digest, count = hashlib.sha256(), 0
        with snapshot.path.open("rb") as handle:
            first = handle.readline()
            digest.update(first)
            if json.loads(first) != {"header": snapshot.header}:
                raise ValueError("Snapshot header mismatch")
            for line_number, line in enumerate(handle, 2):
                digest.update(line)
                item = json.loads(line)
                table, raw = item["table"], item["row"]
                if table not in snapshot.header["schemas"] or not isinstance(raw, dict):
                    raise ValueError("Unrecognized snapshot record")
                ticker, session = raw.get("symbol"), raw.get("date")
                allowed = {snapshot.header["benchmark"]} if table == "benchmarks" else allowed_prices if table == "prices" else allowed_delivery
                if ticker not in allowed:
                    raise ValueError("Record outside frozen universe selection")
                kind = "delivery" if table == "delivery_data" else "prices"
                identity = (kind, ticker, canonical(session))
                try:
                    conn.execute("INSERT INTO import_identities VALUES(?,?,?)", identity)
                except sqlite3.IntegrityError:
                    raise ValueError("Ambiguous duplicate symbol-session observations") from None
                count += 1
                try:
                    observation = _observation(table, raw, snapshot, calendar, imported_at)
                    payload = observation.model_dump_json()
                    if isinstance(observation, DailyPrice):
                        prices.append((snapshot.snapshot_id, ticker, session, observation.price_basis, payload))
                    else:
                        contexts.append((snapshot.snapshot_id, observation.kind, ticker, session, payload))
                except (ValueError, TypeError, KeyError, ValidationError) as error:
                    code = str(error) if isinstance(error, RejectedRow) else "INVALID_OBSERVATION"
                    rejected[code] += 1
                    failures.append((snapshot.snapshot_id, line_number, ticker,
                                     session if isinstance(session, str) else canonical(session), code))
                if (len(prices) + len(contexts) + len(failures)) >= config.SWING_IMPORT_BATCH_SIZE:
                    flush()
        if digest.hexdigest() != snapshot.snapshot_id or count != snapshot.rows:
            raise ValueError("Snapshot changed while importing")
        flush()
        result = coverage(conn, snapshot, config, rejected)
        completed_at = utc(clock.now())
        if completed_at < imported_at:
            raise ValueError("Import clock moved backwards")
        conn.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?)", (snapshot.snapshot_id, snapshot.source_uri,
            snapshot.observed_at.isoformat(), completed_at.isoformat(), str(snapshot.path.resolve()), canonical(snapshot.header)))
        report = {**result, "observed_at": snapshot.observed_at.isoformat(),
                  "imported_at": completed_at.isoformat(), "reused": False}
        conn.execute("INSERT INTO reports VALUES(?,?)", (snapshot.snapshot_id, canonical(report)))
        return report

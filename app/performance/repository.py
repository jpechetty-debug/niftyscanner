"""Versioned SQLite extension. Original scans/signals are never rewritten."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager


def strategy_context(config) -> dict:
    keys = ("RSI_PERIOD", "MIN_RSI", "RSI_CAP", "REQUIRE_RSI_TREND_UP",
            "MIN_VOLUME_RATIO", "VOLUME_RATIO_CAP", "VOLUME_LOOKBACK", "MIN_AVG_VOLUME",
            "MIN_AVG_TRADED_VALUE_NSE", "MIN_AVG_TRADED_VALUE_NYSE",
            "MIN_VOLUME_PROJECTION_ELAPSED", "OPTIMAL_PE", "MIN_PE", "MAX_PE",
            "WEIGHT_VOLUME", "WEIGHT_RSI", "WEIGHT_PE", "MAX_BAR_AGE_SESSIONS", "MIN_BARS")
    return {"version": "composite-v1", **{key: getattr(config, key) for key in keys}}


def strategy_id(context: str | None) -> str:
    try:
        value = json.loads(context) if context else None
    except (ValueError, TypeError):
        return "legacy-invalid-context"
    if not value:
        return "legacy-unknown"
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "composite-v1-" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _migrate_v2(conn: sqlite3.Connection, applied: set[int]) -> None:
    if 2 in applied:
        return
    conn.execute("CREATE TABLE IF NOT EXISTS performance_sync "
                 "(id INTEGER PRIMARY KEY CHECK(id=1), last_signal_id INTEGER NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS performance_contexts "
                 "(benchmark TEXT NOT NULL, price_basis TEXT NOT NULL, version TEXT NOT NULL, "
                 "PRIMARY KEY(benchmark,price_basis,version))")
    conn.execute("CREATE INDEX IF NOT EXISTS performance_signal_cursor ON signals(market,id)")
    conn.execute("INSERT INTO performance_schema VALUES (2)")


def migrate(conn: sqlite3.Connection) -> None:
    """One transaction per migration, including version marker; repeatable on restart."""
    try:
        applied = {row[0] for row in conn.execute("SELECT version FROM performance_schema")}
    except sqlite3.OperationalError as error:
        if "no such table" not in str(error):
            raise
        applied = set()
    if {1, 2} <= applied:
        return
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS performance_schema (version INTEGER PRIMARY KEY)")
        applied = {row[0] for row in conn.execute("SELECT version FROM performance_schema")}
        if 1 in applied:
            _migrate_v2(conn, applied)
            return
        if "strategy_context" not in {r[1] for r in conn.execute("PRAGMA table_info(scans)")}:
            conn.execute("ALTER TABLE scans ADD COLUMN strategy_context TEXT")
        statements = [
            """CREATE TABLE signal_cohorts (
                id INTEGER PRIMARY KEY, signal_id INTEGER NOT NULL REFERENCES signals(id),
                market TEXT NOT NULL, ticker TEXT NOT NULL, bar_date TEXT NOT NULL,
                available_at TEXT NOT NULL, entry_session TEXT, strategy TEXT NOT NULL,
                strategy_context TEXT, score REAL, volume_ratio REAL, rsi REAL,
                exclusion TEXT NOT NULL DEFAULT '',
                UNIQUE(market,ticker,bar_date,strategy,exclusion))""",
            """CREATE TABLE signal_outcomes (
                cohort_id INTEGER NOT NULL REFERENCES signal_cohorts(id), horizon INTEGER NOT NULL,
                exit_session TEXT, exit_at TEXT, status TEXT NOT NULL, reason TEXT,
                stock_entry REAL, stock_exit REAL, benchmark_entry REAL, benchmark_exit REAL,
                stock_return REAL, benchmark_return REAL, excess_return REAL,
                benchmark TEXT NOT NULL, price_basis TEXT NOT NULL, vintage TEXT,
                evaluated_at TEXT, last_attempt TEXT, evaluation_version TEXT NOT NULL,
                PRIMARY KEY(cohort_id,horizon,benchmark,price_basis,evaluation_version))""",
            """CREATE TABLE performance_prices (
                ticker TEXT NOT NULL, session TEXT NOT NULL, price_basis TEXT NOT NULL,
                vintage TEXT NOT NULL, open REAL, close REAL, dividend REAL, split REAL,
                PRIMARY KEY(ticker,session,price_basis,vintage))""",
            """CREATE TABLE performance_jobs (
                due_date TEXT PRIMARY KEY, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                started_at TEXT, completed_at TEXT, retry_at TEXT, error TEXT,
                processed INTEGER NOT NULL DEFAULT 0)""",
            "CREATE INDEX outcomes_due ON signal_outcomes(status,exit_at,last_attempt)",
            "CREATE INDEX signals_daily ON signals(market,bar_date,ticker)",
        ]
        for statement in statements:
            conn.execute(statement)
        conn.execute("INSERT INTO performance_schema VALUES (1)")
        _migrate_v2(conn, {1})


class PerformanceRepository:
    def __init__(self, data_dir: str = "data", busy_timeout_sec: float | None = None):
        self.data_dir = data_dir
        self.busy_timeout_sec = busy_timeout_sec

    @contextmanager
    def connection(self):
        from app.cache.persistence import _get_db_connection
        conn = _get_db_connection(self.data_dir, busy_timeout_sec=self.busy_timeout_sec)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

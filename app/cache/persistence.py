"""Atomic JSON persistence for last-scan results and application settings."""

from __future__ import annotations

import sqlite3
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional
from loguru import logger

SCHEMA_VERSION = 1

def _get_db_connection(data_dir: str) -> sqlite3.Connection:
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    db_path = Path(data_dir) / "history.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    return conn

def _init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT,
            timestamp TEXT,
            scan_seconds REAL,
            request_count INTEGER,
            universe INTEGER,
            fetched INTEGER,
            failed INTEGER,
            filtered_rsi INTEGER,
            filtered_rsi_trend INTEGER,
            filtered_volume INTEGER,
            filtered_liquidity INTEGER,
            passed_rsi_volume INTEGER,
            filtered_pe INTEGER,
            passed_pe INTEGER
        );
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER,
            ticker TEXT,
            name TEXT,
            market TEXT,
            price REAL,
            pe REAL,
            rsi REAL,
            rsi_trend REAL,
            volume INTEGER,
            avg_volume_20d REAL,
            volume_ratio REAL,
            score REAL,
            session_partial INTEGER,
            bar_date TEXT,
            FOREIGN KEY(scan_id) REFERENCES scans(id)
        );
        CREATE TABLE IF NOT EXISTS failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER,
            ticker TEXT,
            stage TEXT,
            code TEXT,
            message TEXT,
            FOREIGN KEY(scan_id) REFERENCES scans(id)
        );
    """)
    conn.commit()


def atomic_write_json(file_path: Path | str, data: Dict[str, Any]) -> None:
    """Atomically write JSON data using a temporary file and replace."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to a temporary file in the same directory to ensure atomic same-filesystem rename
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        prefix="atomic_",
        suffix=".tmp",
    ) as tf:
        json.dump(data, tf, indent=2)
        tf.flush()
        os.fsync(tf.fileno())
        temp_name = tf.name

    # Atomic replace (works on Windows & POSIX when temp file is closed)
    os.replace(temp_name, path)


def save_last_scan(market: str, payload: Dict[str, Any], data_dir: str = "data") -> Path:
    """Persist successful scan payload to SQLite database."""
    conn = _get_db_connection(data_dir)
    try:
        cursor = conn.cursor()
        meta = payload.get("meta", {})
        funnel = meta.get("funnel", {})
        
        cursor.execute("""
            INSERT INTO scans (
                market, timestamp, scan_seconds, request_count,
                universe, fetched, failed, filtered_rsi, filtered_rsi_trend,
                filtered_volume, filtered_liquidity, passed_rsi_volume,
                filtered_pe, passed_pe
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            market.upper(),
            meta.get("last_refreshed"),
            meta.get("scan_seconds", 0.0),
            meta.get("request_count", 0),
            funnel.get("universe", 0),
            funnel.get("fetched", 0),
            funnel.get("failed", 0),
            funnel.get("filtered_rsi", 0),
            funnel.get("filtered_rsi_trend", 0),
            funnel.get("filtered_volume", 0),
            funnel.get("filtered_liquidity", 0),
            funnel.get("passed_rsi_volume", 0),
            funnel.get("filtered_pe", 0),
            funnel.get("passed_pe", 0)
        ))
        scan_id = cursor.lastrowid
        
        signals = payload.get("results", [])
        for sig in signals:
            cursor.execute("""
                INSERT INTO signals (
                    scan_id, ticker, name, market, price, pe, rsi, rsi_trend,
                    volume, avg_volume_20d, volume_ratio, score, session_partial, bar_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                scan_id, sig.get("ticker"), sig.get("name"), sig.get("market"),
                sig.get("price"), sig.get("pe"), sig.get("rsi"), sig.get("rsi_trend"),
                sig.get("volume"), sig.get("avg_volume_20d"), sig.get("volume_ratio"),
                sig.get("score"), 1 if sig.get("session_partial") else 0, sig.get("bar_date")
            ))
            
        failures = payload.get("failed_symbols", [])
        for f in failures:
            cursor.execute("""
                INSERT INTO failures (scan_id, ticker, stage, code, message)
                VALUES (?, ?, ?, ?, ?)
            """, (scan_id, f.get("ticker"), f.get("stage"), f.get("code"), f.get("message")))
            
        conn.commit()
        logger.info(f"Persisted scan results for {market} to SQLite database.")
        return Path(data_dir) / "history.db"
    finally:
        conn.close()


def load_last_scan_on_startup(market: str, data_dir: str = "data") -> Optional[Dict[str, Any]]:
    """Load persisted scan from SQLite on startup, marking stale = True."""
    db_path = Path(data_dir) / "history.db"
    if not db_path.exists():
        return None

    conn = _get_db_connection(data_dir)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM scans WHERE market = ? ORDER BY id DESC LIMIT 1",
            (market.upper(),)
        )
        scan = cursor.fetchone()
        if not scan:
            return None
            
        scan_id = scan["id"]
        
        cursor.execute("SELECT * FROM signals WHERE scan_id = ?", (scan_id,))
        signals = [dict(r) for r in cursor.fetchall()]
        for sig in signals:
            sig["session_partial"] = bool(sig["session_partial"])
            
        cursor.execute("SELECT * FROM failures WHERE scan_id = ?", (scan_id,))
        failures = [dict(r) for r in cursor.fetchall()]
        
        payload = {
            "schema_version": SCHEMA_VERSION,
            "meta": {
                "stale": True,
                "stale_reasons": ["Loaded from disk on startup; awaiting initial scan"],
                "last_refreshed": scan["timestamp"],
                "scan_seconds": scan["scan_seconds"],
                "request_count": scan["request_count"],
                "funnel": {
                    "universe": scan["universe"],
                    "fetched": scan["fetched"],
                    "failed": scan["failed"],
                    "filtered_rsi": scan["filtered_rsi"],
                    "filtered_rsi_trend": scan["filtered_rsi_trend"],
                    "filtered_volume": scan["filtered_volume"],
                    "filtered_liquidity": scan["filtered_liquidity"],
                    "passed_rsi_volume": scan["passed_rsi_volume"],
                    "filtered_pe": scan["filtered_pe"],
                    "passed_pe": scan["passed_pe"],
                }
            },
            "results": signals,
            "failed_symbols": failures,
        }
        
        logger.info(f"Loaded initial scan state from SQLite for {market} (marked stale).")
        return payload
    except Exception as e:
        logger.warning(f"Failed to load previous scan from SQLite for {market}: {e}")
        return None
    finally:
        conn.close()


def save_settings_file(settings_data: Dict[str, Any], file_path: str = "data/settings.json") -> Path:
    """Persist updated settings atomically to data/settings.json."""
    p = Path(file_path)
    atomic_write_json(p, settings_data)
    logger.info(f"Saved settings atomically to {p}")
    return p

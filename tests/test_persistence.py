"""Unit tests for atomic persistence and startup state restoration."""

import json
from pathlib import Path
import pytest

from app.cache.persistence import (
    atomic_write_json,
    load_last_scan_on_startup,
    save_last_scan,
    save_settings_file,
)


def test_atomic_write_and_replace(tmp_path: Path):
    """Verify atomic write persists valid JSON without corruption."""
    target_file = tmp_path / "test_data.json"
    data = {"key": "value", "numbers": [1, 2, 3]}

    atomic_write_json(target_file, data)
    assert target_file.exists()

    with open(target_file, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == data


def test_save_last_scan_and_startup_load(tmp_path: Path):
    """Verify persisting last scan results and loading on startup as stale = True."""
    payload = {
        "meta": {
            "market": "NSE",
            "market_status": "open",
            "stale": False,
            "stale_reasons": [],
            "last_refreshed": "2026-10-01T15:30:00+05:30",
            "effective_interval_sec": 60,
            "scan_seconds": 12.5,
            "request_count": 501,
            "funnel": {"universe": 501, "fetched": 500, "passed_pe": 5},
        },
        "results": [
            {
                "ticker": "TEST1.NS",
                "name": "Test Co",
                "market": "NSE",
                "price": 100.0,
                "pe": 15.0,
                "rsi": 65.0,
                "volume": 200000,
                "avg_volume_20d": 100000.0,
                "volume_ratio": 2.0,
                "score": 0.45,
                "session_partial": False,
                "bar_date": "2026-10-01",
            }
        ],
        "failed_symbols": [],
    }

    # Save to custom test directory
    saved_path = save_last_scan(market="NSE", payload=payload, data_dir=str(tmp_path))
    assert saved_path.exists()

    with open(saved_path, "r", encoding="utf-8") as f:
        disk_raw = json.load(f)
    assert disk_raw.get("schema_version") == 1
    assert disk_raw["meta"]["stale"] is False

    # Load on startup -> Must be marked stale = True
    startup_data = load_last_scan_on_startup(market="NSE", data_dir=str(tmp_path))
    assert startup_data is not None
    assert startup_data["meta"]["stale"] is True
    assert any("startup" in r.lower() for r in startup_data["meta"]["stale_reasons"])


def test_save_settings_atomically(tmp_path: Path):
    """Verify settings.json persistence."""
    settings_file = tmp_path / "settings.json"
    save_settings_file({"REFRESH_INTERVAL_SEC": 90}, file_path=str(settings_file))

    with open(settings_file, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["REFRESH_INTERVAL_SEC"] == 90

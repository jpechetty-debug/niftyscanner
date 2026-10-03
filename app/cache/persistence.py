"""Atomic JSON persistence for last-scan results and application settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional
from loguru import logger

SCHEMA_VERSION = 1


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
    """Persist successful scan payload to data/last_scan_{MARKET}.json atomically."""
    file_path = Path(data_dir) / f"last_scan_{market.upper()}.json"
    data_with_schema = {
        "schema_version": SCHEMA_VERSION,
        **payload,
    }
    atomic_write_json(file_path, data_with_schema)
    logger.info(f"Persisted last scan results atomically to {file_path}")
    return file_path


def load_last_scan_on_startup(market: str, data_dir: str = "data") -> Optional[Dict[str, Any]]:
    """Load persisted scan from disk on startup, marking stale = True."""
    file_path = Path(data_dir) / f"last_scan_{market.upper()}.json"
    if not file_path.exists():
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Mark as stale upon startup until first new scan completes
        if "meta" in data and isinstance(data["meta"], dict):
            data["meta"]["stale"] = True
            reasons = data["meta"].get("stale_reasons", [])
            startup_msg = "Loaded from disk on startup; awaiting initial scan"
            if startup_msg not in reasons:
                reasons.append(startup_msg)
            data["meta"]["stale_reasons"] = reasons

        logger.info(f"Loaded initial scan state from {file_path} (marked stale).")
        return data
    except Exception as e:
        logger.warning(f"Failed to load previous scan from {file_path}: {e}")
        return None


def save_settings_file(settings_data: Dict[str, Any], file_path: str = "data/settings.json") -> Path:
    """Persist updated settings atomically to data/settings.json."""
    p = Path(file_path)
    atomic_write_json(p, settings_data)
    logger.info(f"Saved settings atomically to {p}")
    return p

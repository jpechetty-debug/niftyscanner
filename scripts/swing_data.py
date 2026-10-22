"""Swing S1 local public-cache import/report CLI; no network or trading endpoints.

python -m scripts.swing_data import --source <lite.db> --report <coverage.json>
python -m scripts.swing_data report --vintage <sha256>
python -m scripts.swing_data verify --vintage <sha256>
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from app.core.config import Settings, load_settings
from app.core.interfaces import Clock
from app.market.calendar import MarketCalendar
from app.market.clock import SystemClock
from app.providers.swing_archive import export_snapshot, load_selection
from app.swing.data import import_snapshot
from app.swing.repository import SwingRepository


def run_import(config: Settings, clock: Clock, source: Path) -> dict:
    """Public entry point with injectable time/config; source remains read-only."""
    destination = Path(config.SWING_DB_PATH)
    if source.resolve() == destination.resolve() or (source.exists() and destination.exists() and source.samefile(destination)):
        raise ValueError("Source and destination database must differ")
    calendar = MarketCalendar("NSE", config=config)
    selection = load_selection(Path(config.NSE_UNIVERSE_PATH))
    snapshot = export_snapshot(source, Path(config.SWING_ARTIFACT_DIR), selection, calendar, clock,
                               config.SWING_BENCHMARK, config.SWING_SOURCE_TIMEOUT_SEC,
                               config.SWING_MIN_HISTORY_SESSIONS, config.SWING_DELIVERY_SESSIONS)
    repository = SwingRepository(Path(config.SWING_DB_PATH), config.SQLITE_BUSY_TIMEOUT_SEC, calendar)
    return import_snapshot(snapshot, repository, calendar, config, clock)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["import", "report", "verify"])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--universe", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--vintage")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    config, clock = load_settings(), SystemClock()
    overrides = {key: str(value) for key, value in {
        "NSE_UNIVERSE_PATH": args.universe, "SWING_DB_PATH": args.db,
        "SWING_ARTIFACT_DIR": args.artifacts}.items() if value is not None}
    # Re-validate CLI overrides rather than bypassing Settings validation.
    config = type(config)(**{**config.model_dump(), **overrides})
    if args.report:
        protected = [Path(config.SWING_DB_PATH)]
        if args.source:
            protected.append(args.source)
        if config.SWING_SOURCE_DB_PATH:
            protected.append(Path(config.SWING_SOURCE_DB_PATH))
        if args.report.resolve() in {path.resolve() for path in protected}:
            parser.error("Coverage output cannot overwrite source/destination databases")
        if args.report.resolve().is_relative_to(Path(config.SWING_ARTIFACT_DIR).resolve()):
            parser.error("Keep coverage output outside the immutable evidence directory")
    repository = SwingRepository(Path(config.SWING_DB_PATH), config.SQLITE_BUSY_TIMEOUT_SEC)
    try:
        if args.command == "import":
            source = args.source or (Path(config.SWING_SOURCE_DB_PATH) if config.SWING_SOURCE_DB_PATH else None)
            if source is None:
                parser.error("Explicit --source or SWING_SOURCE_DB_PATH required")
            report = run_import(config, clock, source)
        else:
            if not args.vintage:
                parser.error("--vintage required")
            report = repository.verify(args.vintage) if args.command == "verify" else repository.report(args.vintage)
        if args.report:
            # Atomic output so interrupted imports never leave a half-written coverage report.
            from app.cache.persistence import atomic_write_json
            atomic_write_json(args.report, report)
        summary = {key: report[key] for key in ("snapshot_id", "summary", "benchmark", "rejected", "reused", "artifact_verified", "universe_verified") if key in report}
        if "benchmark" in summary:
            summary["benchmark"] = {k: v for k, v in summary["benchmark"].items() if k != "missing_sessions"}
        print(json.dumps(summary, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Swing data error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())

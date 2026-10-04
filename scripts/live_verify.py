"""Run one real NSE scan in the requested window and retain API evidence.

Usage: .venv/Scripts/python.exe -m scripts.live_verify --mode market-hours
       .venv/Scripts/python.exe -m scripts.live_verify --mode post-close
"""

import argparse
import asyncio
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import httpx

from app.core.config import load_settings
from app.market.calendar import MarketCalendar
from app.market.clock import SystemClock
from app.universe.nse import NSEUniverse


async def verify(mode: str, check_only: bool) -> int:
    config = load_settings()
    clock = SystemClock()
    calendar = MarketCalendar("NSE")
    started = clock.now()
    local_date = started.astimezone(calendar.tz).date().isoformat()
    is_session = calendar.calendar.is_session(local_date)
    is_open = calendar.is_market_open(started)
    target = (
        calendar.calendar.session_close(local_date).to_pydatetime()
        + timedelta(minutes=config.MARKET_CLOSE_SCAN_DELAY_MIN)
    ) if is_session else None
    in_window = is_open if mode == "market-hours" else (
        target is not None and not is_open and started >= target
    )
    print(f"NSE {mode}: {started.astimezone(calendar.tz).isoformat()}, in_window={in_window}")
    if not in_window:
        print("Deferred: no Yahoo requests made outside the requested window.")
        return 2
    universe_path = Path(config.NSE_UNIVERSE_PATH)
    symbols = NSEUniverse(universe_path).load()  # Missing real universe must stop the run.
    if check_only:
        print(f"Ready: {len(symbols)} real constituents; no Yahoo requests made.")
        return 0
    evidence_path = Path("logs/live_verification") / f"{local_date}_{mode}.json"
    if evidence_path.exists():
        print(f"Existing attempt retained: {evidence_path}")
        return 0 if json.loads(evidence_path.read_text())["verified"] else 1

    from app.main import create_app

    app = create_app(config=config, start_scheduler=False)
    ok = await app.state.scheduler.execute_scan("NSE")
    completed = clock.now()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        results = await client.get("/api/results", params={"market": "NSE"})
        status = await client.get("/api/status", params={"market": "NSE"})
    results.raise_for_status()
    status.raise_for_status()
    payload = results.json()
    errors = []
    if not ok:
        errors.append("Real scan failed; retained results are not verification evidence.")
    success_at = app.state.state_manager.last_successful_scan_at.get("NSE")
    if success_at is None or success_at < started:
        errors.append("No new successful scan timestamp.")
    if completed.astimezone(calendar.tz).date().isoformat() != local_date:
        errors.append("Scan crossed the session date.")
    if ok and (payload["meta"]["stale"] or payload["meta"]["data_as_of"][:10] != local_date):
        errors.append("Feed did not provide fresh data for this session.")
    if mode == "market-hours" and not calendar.is_market_open(completed):
        errors.append("Scan did not complete during market hours.")
    for item in payload["results"] if ok else []:
        expected = calendar.is_session_partial(date.fromisoformat(item["bar_date"]), completed)
        if item["session_partial"] != expected:
            errors.append(f"session_partial mismatch: {item['ticker']}")
    if ok and mode == "post-close" and status.json()["next_refresh_at"] is not None:
        errors.append("Successful final scan still has a pending refresh.")
    evidence = {
        "mode": mode, "session_date": local_date,
        "started_at": started.isoformat(), "completed_at": completed.isoformat(),
        "provider": "YFinanceProvider", "clock": "SystemClock",
        "universe_path": str(universe_path.resolve()), "universe_count": len(symbols),
        "universe_sha256": hashlib.sha256(universe_path.read_bytes()).hexdigest(),
        "scan_succeeded": ok, "verified": not errors, "validation_errors": errors,
        "results_http_status": results.status_code, "status_http_status": status.status_code,
        "results": payload, "status": status.json(),
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"evidence": str(evidence_path.resolve()), "verified": not errors,
                      "errors": errors, "meta": payload["meta"]}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=["market-hours", "post-close"])
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(verify(args.mode, args.check_only)))

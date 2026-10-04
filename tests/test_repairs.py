"""SYNTHETIC regression checks for the CodeGraph review; no network calls."""

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import httpx
import pandas as pd
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.routes import router
from app.cache.persistence import load_last_scan_on_startup, save_last_scan
from app.core.config import Settings, load_settings
from app.core.indicators import clean_and_validate_bars
from app.core.outcomes import FailureCode, FunnelCounts
from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock
from app.providers.fake_provider import FakeProvider
from app.providers.yfinance_provider import YFinanceProvider
from app.scheduler.runner import Scheduler
from app.services.scanner import StockScannerService
from app.services.state import ScanStateManager
from app.universe.nse import NSEUniverse
from tests.fixtures.synthetic_data import SYNTHETIC_UNIVERSE, generate_synthetic_ohlcv


def setup_scheduler(markets="NSE"):
    cfg = Settings(_env_file=None, ENABLED_MARKETS=markets)
    clock = FakeClock(datetime(2026, 10, 1, 6, tzinfo=timezone.utc))
    calendars = {m: MarketCalendar(m) for m in cfg.enabled_markets_list}
    state = ScanStateManager(cfg, {}, {}, calendars=calendars, clock=clock)
    universe = NSEUniverse("tests/fixtures/nifty500_synthetic.csv")
    scanners = {m: Mock() for m in cfg.enabled_markets_list}
    for scanner in scanners.values():
        scanner.data_as_of = "2026-09-30T00:00:00+05:30"
        scanner.run_scan.return_value = ([], FunnelCounts(), [], 0, 45.0, True)
    scheduler = Scheduler(cfg, state, scanner_services=scanners,
                          universes={m: universe for m in scanners}, calendars=calendars, clock=clock)
    return cfg, clock, state, scheduler, scanners


@pytest.mark.asyncio
async def test_real_open_loop_uses_scan_completion(monkeypatch):
    _, clock, state, scheduler, scanners = setup_scheduler()
    starts = []

    def scan(_):
        starts.append(clock.now())
        clock.sleep(45)
        if len(starts) == 2:
            scheduler._stop_event.set()
        return [], FunnelCounts(), [], 0, 45.0, True

    scanners["NSE"].run_scan.side_effect = scan
    waits = iter([89, 1, 0])

    async def advance(waiter, timeout):
        waiter.close()
        clock.sleep(next(waits))
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", advance)
    await scheduler._run_loop()
    assert len(starts) == 2
    assert (starts[1] - starts[0]).total_seconds() == 135
    assert state.last_scan_completed_at["NSE"] == clock.now()


@pytest.mark.asyncio
async def test_post_close_failure_is_attempted_once(monkeypatch):
    _, clock, _, scheduler, scanners = setup_scheduler()
    scanners["NSE"].run_scan.return_value = ([], FunnelCounts(), [], 0, 0.0, False)
    waits = 0

    async def advance(waiter, timeout):
        nonlocal waits
        waiter.close()
        waits += 1
        clock.set_time(datetime(2026, 10, 1, 10, 20 + waits - 1, tzinfo=timezone.utc))
        if waits == 3:
            scheduler._stop_event.set()
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", advance)
    await scheduler._run_loop()
    assert scanners["NSE"].run_scan.call_count == 2  # startup + post-close


@pytest.mark.asyncio
async def test_manual_scan_after_close_satisfies_scheduled_attempt(monkeypatch):
    _, clock, state, scheduler, scanners = setup_scheduler()
    waits = 0

    async def advance(waiter, timeout):
        nonlocal waits
        waiter.close()
        waits += 1
        clock.set_time(datetime(2026, 10, 1, 10, 21, tzinfo=timezone.utc))
        state.last_scan_completed_at["NSE"] = clock.now()
        if waits == 2:
            scheduler._stop_event.set()
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", advance)
    await scheduler._run_loop()
    assert scanners["NSE"].run_scan.call_count == 1  # startup only


@pytest.mark.asyncio
async def test_global_reservation_shutdown_and_real_api_responsiveness():
    cfg, clock, state, scheduler, scanners = setup_scheduler("NSE,NYSE")
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()

    def blocking_scan(_):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5), "Test release was not signalled"
        return [], FunnelCounts(), [], 0, 0.0, True

    scanners["NSE"].run_scan.side_effect = blocking_scan
    assert scheduler.trigger_immediate_scan("NSE")[0]
    assert not scheduler.trigger_immediate_scan("NSE")[0]
    assert not scheduler.trigger_immediate_scan("NYSE")[0]
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert not await scheduler.execute_scan("NYSE")
        app = FastAPI()
        app.include_router(router)
        app.state.config, app.state.clock = cfg, clock
        app.state.state_manager, app.state.scheduler = state, scheduler
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await asyncio.wait_for(client.get("/api/results"), 1)
            assert response.status_code == 200
            denied = await client.post("/api/refresh?market=NYSE")
            assert denied.status_code == 429
            assert denied.json()["detail"]["retry_after"] > 0
        stopping = asyncio.create_task(scheduler.stop())
        tick = loop.create_future()
        loop.call_soon(tick.set_result, None)
        await tick
        assert not stopping.done()
    finally:
        release.set()
        await scheduler.stop()
    await stopping
    assert not state.scan_lock.locked()
    assert not any(state.is_scanning.values())


def test_next_deadline_is_stable_and_settings_reschedule():
    cfg, clock, state, _, _ = setup_scheduler()
    state.last_scan_completed_at["NSE"] = clock.now()
    state.data_as_of["NSE"] = "2026-09-30T00:00:00+05:30"
    first = state.get_results_payload("NSE")["meta"]
    clock.sleep(10)
    second = state.get_results_payload("NSE")["meta"]
    assert first["next_refresh_at"] == second["next_refresh_at"]
    assert second["data_as_of"] == "2026-09-30T00:00:00+05:30"
    cfg.REFRESH_INTERVAL_SEC = 120
    assert state.get_next_refresh_at("NSE") == state.last_scan_completed_at["NSE"] + timedelta(seconds=120)
    clock.set_time(datetime(2026, 10, 1, 10, 5, tzinfo=timezone.utc))
    assert state.get_next_refresh_at("NSE") == datetime(2026, 10, 1, 10, 20, tzinfo=timezone.utc)
    state.last_scan_completed_at["NSE"] = datetime(2026, 10, 1, 10, 20, tzinfo=timezone.utc)
    assert state.get_next_refresh_at("NSE") is None


def test_breaker_open_aborts_scan_and_stops_dispatch(monkeypatch):
    cfg = Settings(_env_file=None, ENABLED_MARKETS="NSE", PE_WORKERS=1, BREAKER_THRESHOLD=1)
    clock = FakeClock(datetime(2026, 10, 1, 10, tzinfo=timezone.utc))
    provider = YFinanceProvider(cfg, clock=clock)
    fake = FakeProvider(print_banner=False)
    monkeypatch.setattr(provider, "download_bars", fake.download_bars)
    fetch = Mock(side_effect=lambda t: (t, None, FailureCode.PE_FETCH_FAILED, "synthetic timeout", True))
    monkeypatch.setattr(provider, "_fetch_single_pe", fetch)
    scan = StockScannerService(cfg, provider, MarketCalendar("NSE"), clock)
    outcome = scan.run_scan(SYNTHETIC_UNIVERSE)
    assert not outcome[-1]
    assert fetch.call_count == 1
    assert sum(f.code == FailureCode.CIRCUIT_OPEN for f in outcome[2]) == 1


def test_half_open_only_dispatches_one_trial_then_recovers(monkeypatch):
    cfg = Settings(_env_file=None, PE_WORKERS=3, BREAKER_THRESHOLD=1)
    clock = FakeClock()
    provider = YFinanceProvider(cfg, clock=clock)
    provider.pe_breaker.record_systemic_failure("synthetic outage")
    clock.sleep(cfg.BREAKER_COOLDOWN_SEC + 1)
    states = []

    def fetch(ticker):
        states.append(provider.pe_breaker.state.value)
        return ticker, None, FailureCode.MISSING_PE, "synthetic missing", False

    monkeypatch.setattr(provider, "_fetch_single_pe", fetch)
    provider.fetch_pe_batch([s.ticker for s in SYNTHETIC_UNIVERSE[:3]])
    assert states.count("HALF_OPEN") == 1
    assert states.count("CLOSED") == 2


def test_download_and_pe_retry_accounting(monkeypatch):
    cfg = Settings(_env_file=None, MAX_RETRIES=2)
    provider = YFinanceProvider(cfg, clock=FakeClock())
    ticker = SYNTHETIC_UNIVERSE[0].ticker
    download = Mock(side_effect=[pd.DataFrame(), generate_synthetic_ohlcv(ticker)])
    monkeypatch.setattr("app.providers.yfinance_provider.yf.download", download)
    _, requests, _ = provider.download_bars([ticker])
    assert requests == download.call_count == 2
    fetch = Mock(side_effect=[(ticker, None, FailureCode.PE_FETCH_FAILED, "synthetic timeout", True),
                              (ticker, 15.0, None, None, False)])
    monkeypatch.setattr(provider, "_fetch_single_pe", fetch)
    results, requests, failures = provider.fetch_pe_batch([ticker])
    assert results == {ticker: 15.0} and not failures
    assert requests == fetch.call_count == 2


def test_naive_nyse_bar_dates_are_preserved():
    frame = generate_synthetic_ohlcv(SYNTHETIC_UNIVERSE[0].ticker)
    frame.index = pd.date_range("2026-04-01", periods=len(frame), freq="B")
    calendar = MarketCalendar("NYSE")
    cleaned, code, _ = clean_and_validate_bars(frame, datetime(2026, 10, 1, 16, tzinfo=timezone.utc),
                                               calendar, max_bar_age_sessions=1000)
    assert code is None
    assert cleaned.index[-1].date() == frame.index[-1].date()


def test_data_as_of_survives_empty_screening_results():
    cfg = Settings(_env_file=None, ENABLED_MARKETS="NSE", MIN_AVG_VOLUME=1e12)
    scan = StockScannerService(cfg, FakeProvider(print_banner=False), MarketCalendar("NSE"),
                              FakeClock(datetime(2026, 10, 1, 10, tzinfo=timezone.utc)))
    assert scan.run_scan(SYNTHETIC_UNIVERSE)[0] == []
    assert scan.data_as_of.startswith("2026-10-01")


def test_snapshot_restores_metadata_and_checks_schema(tmp_path):
    payload = {"meta": {"data_as_of": "2026-09-30T00:00:00+05:30"}, "results": [], "failed_symbols": []}
    save_last_scan("NSE", payload, str(tmp_path))
    assert (tmp_path / "last_scan_NSE.json").exists()
    restored = load_last_scan_on_startup("NSE", str(tmp_path))
    assert restored["schema_version"] == 1
    assert restored["meta"]["data_as_of"] == payload["meta"]["data_as_of"]
    assert restored["meta"]["stale"]


def test_open_download_breaker_records_one_wildcard_and_no_requests(monkeypatch):
    provider = YFinanceProvider(Settings(_env_file=None, BREAKER_THRESHOLD=1), clock=FakeClock())
    provider.download_breaker.record_systemic_failure("SYNTHETIC outage")
    download = Mock()
    monkeypatch.setattr("app.providers.yfinance_provider.yf.download", download)
    symbols = [s.ticker for s in SYNTHETIC_UNIVERSE]
    _, count, failures = provider.download_bars(symbols)
    assert count == 0 and not download.called
    assert len(failures) == 1 and failures[0].code == FailureCode.CIRCUIT_OPEN
    assert failures[0].ticker == "*" and failures[0].affected_count == len(symbols)
    assert "affected_count" not in failures[0].model_dump()


@pytest.mark.asyncio
async def test_failed_scan_keeps_previous_results_and_bar_timestamp():
    _, _, state, scheduler, scanners = setup_scheduler()
    await scheduler.execute_scan("NSE")
    previous = state.results_store["NSE"]
    scanners["NSE"].data_as_of = "2026-10-01T00:00:00+05:30"
    scanners["NSE"].run_scan.return_value = ([], FunnelCounts(), [], 5, 10.0, False)
    assert not await scheduler.execute_scan("NSE")
    assert state.results_store["NSE"] is previous
    assert state.data_as_of["NSE"] == "2026-09-30T00:00:00+05:30"
    assert state.evaluate_stale("NSE")[0]


@pytest.mark.asyncio
async def test_settings_write_failure_preserves_runtime(monkeypatch):
    cfg, clock, state, scheduler, _ = setup_scheduler()
    app = FastAPI()
    app.include_router(router)
    app.state.config, app.state.clock = cfg, clock
    app.state.state_manager, app.state.scheduler = state, scheduler
    monkeypatch.setattr("app.api.routes.save_settings_file", Mock(side_effect=OSError("SYNTHETIC disk failure")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        with pytest.raises(OSError):
            await client.put("/api/settings", json={"refresh_interval_sec": 120})
    assert cfg.REFRESH_INTERVAL_SEC == 60


def test_unknown_snapshot_schema_uses_legacy_history(tmp_path):
    import json
    payload = {"meta": {"last_refreshed": "2026-10-01T10:00:00+00:00"}, "results": [], "failed_symbols": []}
    save_last_scan("NSE", payload, str(tmp_path))
    snapshot = tmp_path / "last_scan_NSE.json"
    snapshot.write_text(json.dumps({**payload, "schema_version": 999}))
    assert load_last_scan_on_startup("NSE", str(tmp_path))["meta"]["last_refreshed"] == payload["meta"]["last_refreshed"]


@pytest.mark.parametrize("values", [{"CHUNK_SIZE": 0}, {"PE_WORKERS": 0}, {"MAX_RETRIES": 0},
                                  {"SCAN_MIN_FETCH_RATIO": 1.1}, {"WEIGHT_PE": -0.25},
                                  {"CHUNK_DELAY_MIN_SEC": 3}, {"OPTIMAL_PE": 50}, {"MIN_PE": float("nan")}])
def test_invalid_operational_config_fails_fast(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_invalid_saved_interval_does_not_leak_into_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("REFRESH_INTERVAL_SEC", "60")
    settings = tmp_path / "settings.json"
    settings.write_text('{"REFRESH_INTERVAL_SEC": 1}')
    assert load_settings(settings).REFRESH_INTERVAL_SEC == 60


def test_streamlit_renders_real_countdown_without_network(monkeypatch):
    import importlib
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    monkeypatch.syspath_prepend(str(Path("ui").resolve()))
    client = importlib.import_module("api_client").ScreenerApiClient
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    monkeypatch.setattr(client, "get_status", lambda *a, **k: ({
        "market_status": {"is_open": True}, "is_scanning": False,
        "effective_interval_sec": 60, "next_refresh_at": deadline}, None))
    monkeypatch.setattr(client, "get_settings", lambda *a, **k: (60, None))
    monkeypatch.setattr(client, "get_results", lambda *a, **k: ({
        "meta": {"market": "NSE", "stale": False, "funnel": {}},
        "results": [], "failed_symbols": []}, None))
    page = AppTest.from_file("ui/app.py").run()
    assert not page.exception
    assert any("Next refresh in" in caption.value for caption in page.caption)

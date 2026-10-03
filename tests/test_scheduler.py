"""Unit tests for the screening scheduler, market-hours gating, and lock semantics."""

import asyncio
from datetime import datetime, timezone
import pytest

from app.core.circuit_breaker import CircuitBreaker
from app.core.config import Settings
from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock
from app.providers.fake_provider import FakeProvider
from app.scheduler.runner import Scheduler
from app.services.scanner import StockScannerService
from app.services.state import ScanStateManager
from app.universe.nse import NSEUniverse


@pytest.mark.asyncio
async def test_scheduler_single_flight_and_cooldown():
    """Verify single-flight lock prevents concurrent scans and cooldown returns retry_after."""
    config = Settings(REFRESH_INTERVAL_SEC=60, REFRESH_COOLDOWN_SEC=30)
    clock = FakeClock(datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc))
    calendar = MarketCalendar("NSE")
    provider = FakeProvider(print_banner=False)
    universe = NSEUniverse("tests/fixtures/nifty500_synthetic.csv")

    scanner = StockScannerService(config=config, provider=provider, calendar=calendar, clock=clock)
    cb_down = CircuitBreaker("d", clock=clock)
    cb_pe = CircuitBreaker("p", clock=clock)
    state = ScanStateManager(config=config, download_breaker=cb_down, pe_breaker=cb_pe, calendar=calendar, clock=clock)

    scheduler = Scheduler(
        config=config,
        scanner_service=scanner,
        state_manager=state,
        universe_loader=universe,
        calendar=calendar,
        clock=clock,
    )

    # 1. Execute initial scan
    success = await scheduler.execute_scan()
    assert success is True
    assert state.last_successful_scan_at is not None

    # 2. Check cooldown immediately after scan (0 seconds elapsed < 30s cooldown)
    allowed, retry_after = scheduler.can_trigger_manual_refresh()
    assert allowed is False
    assert retry_after is not None
    assert retry_after <= 30

    # 3. Advance clock by 35s (exceeds 30s cooldown) -> Allowed
    clock.sleep(35.0)
    allowed, retry_after = scheduler.can_trigger_manual_refresh()
    assert allowed is True
    assert retry_after is None


def test_effective_interval_calculation():
    """effective_interval = max(REFRESH_INTERVAL_SEC, 2 x last_scan_seconds)."""
    config = Settings(REFRESH_INTERVAL_SEC=60)
    clock = FakeClock()
    calendar = MarketCalendar("NSE")
    cb_down = CircuitBreaker("d", clock=clock)
    cb_pe = CircuitBreaker("p", clock=clock)
    state = ScanStateManager(config=config, download_breaker=cb_down, pe_breaker=cb_pe, calendar=calendar, clock=clock)

    # When last scan took 10s: 2 x 10 = 20 < 60 -> effective = 60
    state.last_scan_seconds = 10.0
    assert state.effective_interval_sec == 60

    # When last scan took 45s: 2 x 45 = 90 > 60 -> effective = 90
    state.last_scan_seconds = 45.0
    assert state.effective_interval_sec == 90


def test_stale_evaluation_rules():
    """Verify stale = true only when: breaker open, last scan failed, or age > 3x effective_interval."""
    config = Settings(REFRESH_INTERVAL_SEC=60)
    # 06:00 UTC = 11:30 IST Thursday -> market OPEN, so the 3x-interval rule applies
    clock = FakeClock(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    calendar = MarketCalendar("NSE")
    cb_down = CircuitBreaker("d", clock=clock)
    cb_pe = CircuitBreaker("p", clock=clock)
    state = ScanStateManager(config=config, download_breaker=cb_down, pe_breaker=cb_pe, calendar=calendar, clock=clock)

    # 1. Initially no successful scan -> stale
    is_stale, reasons = state.evaluate_stale()
    assert is_stale is True

    # 2. After a success -> fresh
    from app.core.outcomes import FunnelCounts
    state.update_scan_success("NSE", results=[], funnel=FunnelCounts(), failures=[], scan_seconds=10.0, request_count=1)
    is_stale, reasons = state.evaluate_stale()
    assert is_stale is False

    # 3. Last scan failed -> stale
    state.last_scan_failed = True
    is_stale, reasons = state.evaluate_stale()
    assert is_stale is True
    state.last_scan_failed = False

    # 4. Breaker open -> stale
    cb_down.record_systemic_failure()
    cb_down._state = cb_down._state.__class__.OPEN
    is_stale, reasons = state.evaluate_stale()
    assert is_stale is True
    cb_down.reset()

    # 5. Last success older than 3 x effective_interval (3 x 60 = 180s)
    clock.sleep(190.0)
    is_stale, reasons = state.evaluate_stale()
    assert is_stale is True
    assert any("exceeds 3x effective interval" in r for r in reasons)

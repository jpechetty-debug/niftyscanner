"""Integration tests for StockScannerService using FakeProvider and synthetic fixtures."""

from datetime import datetime, timezone
import pytest

from app.core.config import Settings
from app.core.outcomes import FailureCode, Stage
from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock
from app.providers.fake_provider import FakeProvider
from app.services.scanner import StockScannerService
from tests.fixtures.synthetic_data import SYNTHETIC_UNIVERSE


def test_scanner_end_to_end_synthetic():
    """Verify screening execution, funnel tracking, and filtered vs failed separation."""
    config = Settings()
    provider = FakeProvider(print_banner=False)
    clock = FakeClock(datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc))
    calendar = MarketCalendar("NSE")

    scanner = StockScannerService(
        config=config,
        provider=provider,
        calendar=calendar,
        clock=clock,
    )

    results, funnel, failures, req_count, scan_seconds, is_success = scanner.run_scan(SYNTHETIC_UNIVERSE)
    assert is_success is True

    # 1. Total Universe
    assert funnel.universe == 10
    assert funnel.fetched == 10

    # 2. Failed symbols check
    failed_tickers = {f.ticker: f for f in failures}
    assert "TEST8.NS" in failed_tickers
    assert failed_tickers["TEST8.NS"].code == FailureCode.STALE_BAR
    assert failed_tickers["TEST8.NS"].stage == Stage.INDICATORS

    assert "TEST9.NS" in failed_tickers
    assert failed_tickers["TEST9.NS"].code == FailureCode.FLAT_SERIES

    assert "TEST10.NS" in failed_tickers
    assert failed_tickers["TEST10.NS"].code == FailureCode.MISSING_VOLUME

    # Failed count matches failures list length
    assert funnel.failed == len(failures)

    # 3. Filtered check:
    # Filtered symbols must NEVER be in failed_symbols
    assert "TEST4.NS" not in failed_tickers  # Filtered by RSI
    assert "TEST5.NS" not in failed_tickers  # Filtered by Volume
    assert "TEST6.NS" not in failed_tickers  # Filtered by PE

    # 4. Results check:
    # Survivors: TEST1, TEST2, TEST3
    result_tickers = [r.ticker for r in results]
    assert "TEST3.NS" in result_tickers
    assert "TEST1.NS" in result_tickers
    assert "TEST2.NS" in result_tickers

    # TEST3.NS has best breakout and valuation, should be ranked #1
    assert result_tickers[0] == "TEST3.NS"

    # All results must have valid scores, positive volume ratio, and valid RSI
    for r in results:
        assert r.score >= 0.0
        assert r.volume_ratio > config.MIN_VOLUME_RATIO
        assert r.rsi > config.MIN_RSI
        assert r.pe < config.MAX_PE

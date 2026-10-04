"""SYNTHETIC regressions for NYSE exclusions, turnover and EPS classification."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.core.config import Settings
from app.core.filters import apply_stage1_filters
from app.core.fundamentals import NonPositiveEarnings
from app.core.indicators import compute_indicators
from app.core.interfaces import UniverseSymbol
from app.core.outcomes import FailureCode, FunnelTracker
from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock
from app.providers.yfinance_provider import YFinanceProvider
from app.services.scanner import StockScannerService
from app.universe.nyse import NYSEUniverse
from tests.test_filters import make_dummy_indicator


def test_plain_companies_and_reits_survive_targeted_fund_exclusions(tmp_path):
    descriptions = [
        "SYNTHETIC Operating Corporation", "SYNTHETIC Plain Company",
        "SYNTHETIC REIT Common Shares of Beneficial Interest", "SYNTHETIC Defense Properties Trust",
        "SYNTHETIC Term Trust Common Stock", "SYNTHETIC Municipal Common Stock",
        "SYNTHETIC Income Trust Common Shares of Beneficial Interest", "SYNTHETIC Opportunities Common Stock",
        "SYNTHETIC BlackRock Energy Trust Common Stock", "SYNTHETIC Gabelli Value Trust Common Stock",
        "SYNTHETIC Eaton Vance Credit Trust Common Stock",
        "SYNTHETIC Municipals Common Stock", "SYNTHETIC Opportunity Trust Common Stock",
        "SYNTHETIC BlackRock Resources Common Shares of Beneficial Interest",
        "SYNTHETIC Eaton Vance Dividend Common Stock", "SYNTHETIC Royce Value Trust Common Stock",
        "SYNTHETIC STRATS Certificates", "SYNTHETIC Capital Trust",
        "SYNTHETIC BlackRock Inc Common Stock", "SYNTHETIC Franklin BSP Realty Trust Common Stock",
    ]
    p = tmp_path / "SYNTHETIC-universe.txt"
    p.write_text("ACT Symbol|Security Name|Exchange|ETF|Test Issue\n" +
                 "\n".join(f"TEST{i}|{name}|N|N|N" for i, name in enumerate(descriptions)))
    assert {s.symbol for s in NYSEUniverse(p, Settings(_env_file=None)).load()} == {
        "TEST0", "TEST1", "TEST2", "TEST3", "TEST18", "TEST19"}


@pytest.mark.parametrize("market,price,expected", [
    ("NSE", 20, False), ("NSE", 1500, True),
    ("NYSE", 5, False), ("NYSE", 20, True),
])
def test_same_share_count_has_market_specific_money_floor(market, price, expected):
    indicator = make_dummy_indicator(60, 2, 100000)
    indicator.price = price
    indicator.avg_traded_value_20d = price * 100000
    tracker = FunnelTracker()
    assert apply_stage1_filters(indicator, Settings(_env_file=None), tracker, market) == expected
    assert tracker.filtered_liquidity == int(not expected) and tracker.failed == 0


def test_traded_value_uses_paired_completed_bars_not_latest_spike():
    index = pd.date_range(end="2026-10-01", periods=100, freq="B")
    prices = np.linspace(100, 180, 100)
    volumes = np.arange(100) * 1000 + 100000
    frame = pd.DataFrame({"Close": prices, "Volume": volumes}, index=index)
    frame.iloc[-1] = [5000, 9000000]
    previous = frame.iloc[-21:-1]
    result, code, _ = compute_indicators(frame, datetime(2026,10,1,8,tzinfo=timezone.utc), MarketCalendar("NSE"))
    assert code is None and result.session_partial
    assert result.avg_traded_value_20d == pytest.approx((previous.Close * previous.Volume).mean())
    assert result.avg_traded_value_20d != pytest.approx(previous.Volume.mean() * result.price)


def test_money_floor_boundary_optional_share_floor_and_validation():
    cfg, indicator = Settings(_env_file=None), make_dummy_indicator(60, 2)
    indicator.avg_traded_value_20d = cfg.MIN_AVG_TRADED_VALUE_NSE
    assert apply_stage1_filters(indicator, cfg, FunnelTracker())
    indicator.avg_traded_value_20d -= 1
    assert not apply_stage1_filters(indicator, cfg, FunnelTracker())
    cfg.MIN_AVG_TRADED_VALUE_NSE = 0
    cfg.MIN_AVG_VOLUME = indicator.avg_volume_20d + 1
    assert not apply_stage1_filters(indicator, cfg, FunnelTracker())
    for field in ("MIN_AVG_TRADED_VALUE_NSE", "MIN_AVG_TRADED_VALUE_NYSE"):
        for invalid in (-1, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                Settings(_env_file=None, **{field: invalid})


@pytest.mark.parametrize("info,expected", [
    ({"trailingEps": -2}, "filtered"), ({"trailingEps": 0}, "filtered"),
    ({"trailingPE": None, "trailingEps": -2}, "filtered"),
    ({}, "missing"), ({"trailingEps": 2}, "missing"),
    ({"trailingEps": float("nan")}, "missing"), ({"trailingEps": float("inf")}, "missing"),
    ({"trailingEps": "unknown"}, "missing"), ({"trailingEps": False}, "missing"),
    ({"trailingPE": 10, "trailingEps": 2}, "pe"),
    ({"trailingPE": "invalid", "trailingEps": -2}, "invalid"),
])
def test_provider_distinguishes_confirmed_earnings_from_missing_data(monkeypatch, info, expected):
    class Ticker:
        pass
    ticker = Ticker()
    ticker.info = info
    monkeypatch.setattr("app.providers.yfinance_provider.yf.Ticker", lambda symbol: ticker)
    clock = FakeClock(datetime(2026,10,1,11,tzinfo=timezone.utc))
    cfg = Settings(_env_file=None)
    provider = YFinanceProvider(cfg, clock=clock)
    result, attempts, failures = provider.fetch_pe_batch(["TEST1.NS"])
    assert attempts == 1 and provider.pe_breaker.state.value == "CLOSED"
    if expected == "filtered":
        assert isinstance(result["TEST1.NS"], NonPositiveEarnings) and not failures
        again, attempts, failures = provider.fetch_pe_batch(["TEST1.NS"])
        assert again == result and attempts == 0 and not failures
        clock.set_time(clock.now() + timedelta(hours=cfg.PE_CACHE_TTL_HOURS, seconds=1))
        assert provider.fetch_pe_batch(["TEST1.NS"])[1] == 1
    elif expected == "pe":
        assert result == {"TEST1.NS": 10} and not failures
    else:
        assert not result and len(failures) == 1
        assert failures[0].code == (FailureCode.INVALID_PE if expected == "invalid" else FailureCode.MISSING_PE)


def test_confirmed_loss_is_filtered_in_scanner_funnel(monkeypatch):
    class Provider:
        def download_bars(self, symbols):
            return {s: pd.DataFrame({"Close": [100]}, index=pd.DatetimeIndex(["2026-10-01"])) for s in symbols}, len(symbols), []

        def fetch_pe_batch(self, symbols):
            return {symbols[0]: NonPositiveEarnings(-2), symbols[1]: 10}, len(symbols), []

    indicator = make_dummy_indicator(60, 2)
    monkeypatch.setattr("app.services.scanner.compute_indicators", lambda **kw: (indicator, None, None))
    symbols = [UniverseSymbol(symbol=f"TEST{i}",ticker=f"TEST{i}.NS",company_name="SYNTHETIC",market="NSE") for i in (1,2)]
    service = StockScannerService(Settings(_env_file=None), Provider(), MarketCalendar("NSE"), FakeClock())
    results, funnel, failures, _, _, succeeded = service.run_scan(symbols)
    assert succeeded and not failures and funnel.failed == 0
    assert funnel.filtered_pe == 1 and funnel.passed_pe == 1
    assert [r.ticker for r in results] == ["TEST2.NS"]

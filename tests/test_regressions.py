"""Regression tests for bugs found in code review."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app.core.circuit_breaker import CircuitBreaker
from app.core.config import Settings
from app.core.outcomes import FailedSymbolItem, FailureCode, FunnelCounts, Stage
from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock
from app.providers import yfinance_provider as yp
from app.providers.yfinance_provider import YFinanceProvider
from app.services.state import ScanStateManager


def test_download_breaker_recovers_from_half_open(monkeypatch):
    cfg = Settings(CHUNK_DELAY_MIN_SEC=0, CHUNK_DELAY_MAX_SEC=0)
    clk = FakeClock()
    prov = YFinanceProvider(cfg, clock=clk)

    def good(tickers, **kw):
        idx = pd.date_range("2026-04-01", periods=100, freq="B")
        cols = pd.MultiIndex.from_product([["Close", "Volume"], tickers])
        return pd.DataFrame(np.random.rand(100, len(cols)) + 1, index=idx, columns=cols)

    monkeypatch.setattr(yp.yf, "download", good)
    for _ in range(cfg.BREAKER_THRESHOLD):
        prov.download_breaker.record_systemic_failure("x")
    clk.sleep(cfg.BREAKER_COOLDOWN_SEC + 1)  # -> HALF_OPEN

    res, _, fails = prov.download_bars(["A.NS", "B.NS"])
    assert len(res) == 2 and not fails
    assert prov.download_breaker.state.value == "CLOSED"


def test_pe_breaker_trial_released_on_missing_pe(monkeypatch):
    cfg = Settings()
    clk = FakeClock()
    prov = YFinanceProvider(cfg, clock=clk)
    for _ in range(cfg.BREAKER_THRESHOLD):
        prov.pe_breaker.record_systemic_failure("x")
    clk.sleep(cfg.BREAKER_COOLDOWN_SEC + 1)

    class T:
        info = {}  # no trailingPE -> MISSING_PE (non-systemic)

    monkeypatch.setattr(yp.yf, "Ticker", lambda t: T())
    prov.fetch_pe_batch(["A.NS"])
    assert prov.pe_breaker.state.value == "CLOSED"


def test_refresh_triggers_requested_market():
    from app.main import create_app
    from app.providers.fake_provider import FakeProvider
    from app.universe.nse import NSEUniverse

    u = NSEUniverse("tests/fixtures/nifty500_synthetic.csv")
    app = create_app(
        config=Settings(ENABLED_MARKETS="NSE,NYSE"),
        provider=FakeProvider(print_banner=False),
        universe_loader={"NSE": u, "NYSE": u},
        start_scheduler=False,
    )
    called = []

    async def rec(m="NSE"):
        called.append(m)
        return True

    app.state.scheduler.execute_scan = rec
    with TestClient(app) as c:
        assert c.post("/api/refresh?market=NYSE").status_code == 202
        assert c.get("/api/status?market=NYSE").json()["market"] == "NYSE"
    assert called == ["NYSE"]


def test_not_stale_after_hours_once_post_close_scan_done():
    # Fri 2026-10-09 10:20 UTC = 15:50 IST (20 min after close)
    clk = FakeClock(datetime(2026, 10, 9, 10, 20, tzinfo=timezone.utc))
    sm = ScanStateManager(
        Settings(), 
        download_breakers={"NSE": CircuitBreaker("d", clock=clk)}, 
        pe_breakers={"NSE": CircuitBreaker("p", clock=clk)}, 
        clock=clk
    )
    sm._loaded_from_disk_stale = {m: False for m in sm.markets}
    sm.update_scan_success("NSE", [], FunnelCounts(), [], 30.0, 500)
    clk.set_time(clk.now() + timedelta(hours=14))  # next morning, pre-open
    assert sm.evaluate_stale("NSE")[0] is False


def test_failure_list_does_not_grow_across_failed_scans():
    clk = FakeClock()
    sm = ScanStateManager(
        Settings(), 
        download_breakers={"NSE": CircuitBreaker("d", clock=clk)}, 
        pe_breakers={"NSE": CircuitBreaker("p", clock=clk)}, 
        clock=clk
    )
    f = [FailedSymbolItem(ticker="*", stage=Stage.DOWNLOAD, code=FailureCode.EMPTY_CHUNK, message="m")]
    for _ in range(20):
        sm.update_scan_failure("NSE", f, 1.0, 1)
    assert len(sm.failures_store["NSE"]) == 1


# --------------------------------------------------------------------------
# Funnel accounting
# --------------------------------------------------------------------------
def _funnel_total(f):
    return (
        f.failed + f.filtered_rsi + f.filtered_volume + f.filtered_liquidity
        + f.filtered_pe + f.passed_pe
    )


def test_funnel_buckets_sum_to_universe():
    from app.providers.fake_provider import FakeProvider
    from app.services.scanner import StockScannerService
    from tests.fixtures.synthetic_data import SYNTHETIC_UNIVERSE

    clk = FakeClock(datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc))
    for cfg in (Settings(), Settings(MIN_AVG_VOLUME=10**12)):
        svc = StockScannerService(cfg, FakeProvider(print_banner=False), MarketCalendar("NSE"), clk)
        _, funnel, failures, *_ = svc.run_scan(SYNTHETIC_UNIVERSE)
        assert funnel.failed == len(failures)
        assert _funnel_total(funnel) == funnel.universe


def test_symbol_failing_rsi_and_volume_is_counted_once():
    from datetime import date
    from app.core.filters import apply_stage1_filters
    from app.core.indicators import IndicatorResult
    from app.core.outcomes import FunnelTracker

    ind = IndicatorResult(date(2026, 10, 1), 100.0, rsi=30.0, rsi_1d=20.0, volume=1, avg_volume_20d=1.0,
                          volume_ratio=1.0, session_partial=False)
    t = FunnelTracker()
    assert apply_stage1_filters(ind, Settings(), t) is False
    assert (t.filtered_rsi, t.filtered_volume, t.filtered_liquidity) == (1, 0, 0)


def test_min_avg_volume_rejection_is_counted():
    from datetime import date
    from app.core.filters import apply_stage1_filters
    from app.core.indicators import IndicatorResult
    from app.core.outcomes import FunnelTracker

    ind = IndicatorResult(date(2026, 10, 1), 100.0, rsi=60.0, rsi_1d=50.0, volume=500, avg_volume_20d=100.0,
                          volume_ratio=5.0, session_partial=False)
    t = FunnelTracker()
    assert apply_stage1_filters(ind, Settings(MIN_AVG_VOLUME=1000), t) is False
    assert t.filtered_liquidity == 1 and t.passed_rsi_volume == 0


def test_failed_chunk_counts_every_symbol(monkeypatch):
    cfg = Settings(CHUNK_SIZE=3, MAX_RETRIES=1, CHUNK_DELAY_MIN_SEC=0, CHUNK_DELAY_MAX_SEC=0)
    prov = YFinanceProvider(cfg, clock=FakeClock())
    monkeypatch.setattr(yp.yf, "download", lambda **kw: pd.DataFrame())
    tickers = [f"T{i}.NS" for i in range(7)]
    res, _, fails = prov.download_bars(tickers)
    assert res == {}
    assert sum(f.affected_count for f in fails) == len(tickers)
    assert len(fails) == 3
    assert all(f.ticker == "*" for f in fails)


def test_ui_results_section_is_an_auto_refreshing_fragment():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("ui/app.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "results_fragment")
    deco = ast.unparse(fn.decorator_list[0])
    assert "fragment" in deco and "run_every" in deco
    main_src = ast.unparse(next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"))
    assert "get_results" not in main_src  # results must be fetched inside the fragment, not once per rerun

import pandas as pd
import numpy as np

# --------------------------------------------------------------------------
# Calendar / bar cache / dependencies
# --------------------------------------------------------------------------
def test_nse_calendar_knows_weekday_holidays():
    from app.market.calendar import MarketCalendar
    cal = MarketCalendar("NSE")
    # Republic Day (Mon 2026-01-26) and Gandhi Jayanti (Fri 2026-10-02) are exchange holidays
    for d in (datetime(2026, 1, 26, 6, 0, tzinfo=timezone.utc), datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)):
        assert cal.is_market_open(d) is False
        assert cal.get_market_status(d).is_holiday is True


def _bars(closes, start="2026-09-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"Close": closes, "Volume": 1000}, index=idx)


def test_bar_cache_flags_retroactive_price_adjustment():
    from app.cache.bar_cache import BarCache

    cache = BarCache()
    base = _bars([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    cache.set_bars("A.NS", base)

    same = base.tail(3).copy()
    same.iloc[-1, same.columns.get_loc("Close")] = 106.0  # newest bar still moving intraday: ignored
    assert cache.is_consistent("A.NS", same) is True

    adjusted = base.tail(3).copy()
    adjusted["Close"] = adjusted["Close"] * 0.5  # 2:1 split rewrote history
    assert cache.is_consistent("A.NS", adjusted) is False


def test_provider_refetches_full_history_after_adjustment(monkeypatch):
    from app.providers.yfinance_provider import YFinanceProvider
    import app.providers.yfinance_provider as yp
    from app.market.clock import FakeClock
    from app.core.config import Settings
    cfg = Settings(CHUNK_DELAY_MIN_SEC=0, CHUNK_DELAY_MAX_SEC=0)
    prov = YFinanceProvider(cfg, clock=FakeClock())
    old = _bars(list(np.linspace(100, 120, 70)), start="2026-06-01")
    prov.bar_cache.set_bars("A.NS", old)
    calls = []

    def fake_download(tickers, period, **kw):
        calls.append(period)
        halved = old.copy()
        halved["Close"] = halved["Close"] * 0.5  # adjusted history
        df = halved.tail(5) if period == "5d" else halved
        df.columns = pd.MultiIndex.from_product([df.columns, tickers])
        return df

    monkeypatch.setattr(yp.yf, "download", fake_download)
    res, _, fails = prov.download_bars(["A.NS"])
    assert not fails and "6mo" in calls and "5d" in calls
    assert len(res["A.NS"]) == 70
    assert abs(res["A.NS"]["Close"].iloc[0] - 50.0) < 1e-6  # full adjusted history, no splice jump


def test_requirements_cover_imports_and_are_utf8():
    from pathlib import Path

    raw = Path("requirements.txt").read_bytes()
    assert b"\x00" not in raw  # no UTF-16 fragments
    text = raw.decode("utf-8").lower()
    for pkg in ("plotly", "exchange_calendars", "streamlit", "yfinance"):
        assert pkg in text, pkg


def test_scan_during_market_hours_does_not_crash():
    """Partial-session path needs MarketCalendar.get_session_elapsed_fraction (was deleted once)."""
    from app.providers.fake_provider import FakeProvider
    from app.services.scanner import StockScannerService
    from tests.fixtures.synthetic_data import SYNTHETIC_UNIVERSE

    open_dt = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)  # Thu 11:30 IST
    cal = MarketCalendar("NSE")
    assert cal.is_market_open(open_dt)
    svc = StockScannerService(Settings(), FakeProvider(print_banner=False), cal, FakeClock(open_dt))
    _, funnel, failures, *_ = svc.run_scan(SYNTHETIC_UNIVERSE)
    assert funnel.universe == len(SYNTHETIC_UNIVERSE)


def test_session_elapsed_fraction_bounds():
    cal = MarketCalendar("NSE")
    assert cal.get_session_elapsed_fraction(datetime(2026, 10, 1, 10, 30, tzinfo=timezone.utc)) == 1.0  # closed
    mid = cal.get_session_elapsed_fraction(datetime(2026, 10, 1, 6, 52, 30, tzinfo=timezone.utc))      # ~half-way
    assert 0.45 < mid < 0.55
    assert 0.0 < cal.get_session_elapsed_fraction(datetime(2026, 10, 1, 3, 46, tzinfo=timezone.utc)) <= 0.02


def test_volume_projection_floor_is_validated_and_caps_multiplier():
    import pytest
    from pydantic import ValidationError
    from app.core.indicators import compute_volume_metrics

    for bad in (0.0, -1.0, 5.0):
        with pytest.raises(ValidationError, match="MIN_VOLUME_PROJECTION_ELAPSED"):
            Settings(MIN_VOLUME_PROJECTION_ELAPSED=bad)

    vol = pd.Series([100000.0] * 21 + [4000.0])
    _, _, ratio, *_ = compute_volume_metrics(vol, 20, elapsed_fraction=0.01, min_elapsed_fraction=0.25)
    assert abs(ratio - 0.16) < 1e-9  # 4000 / 0.25 / 100000, not 4000 / 0.01 / 100000


def test_bar_cache_forces_refetch_when_delta_does_not_overlap():
    from app.cache.bar_cache import BarCache

    cache = BarCache()
    cache.set_bars("A.NS", _bars([100.0, 101.0, 102.0, 103.0, 104.0]))
    # Next 5d fetch starts weeks later: stitching would silently drop the skipped sessions.
    gapped = _bars([110.0, 111.0, 112.0], start="2026-10-01")
    assert cache.is_consistent("A.NS", gapped) is False


def test_bar_cache_merge_keeps_full_download_length():
    from app.cache.bar_cache import BarCache

    cache = BarCache()
    full = _bars(list(np.linspace(100, 200, 125)), start="2026-04-01")
    cache.update_bars("A.NS", full)
    delta = full.tail(5).copy()
    merged = cache.update_bars("A.NS", delta)
    assert len(merged) == 125  # MIN_BARS up to the 6mo window must survive later scans


def test_inf_volume_is_invalid_volume_not_crash():
    from app.core.indicators import compute_volume_metrics

    *_, code, _ = compute_volume_metrics(pd.Series([1000.0] * 20 + [float("inf")]))
    assert code == FailureCode.INVALID_VOLUME


def test_one_malformed_frame_does_not_abort_scan():
    from app.providers.fake_provider import FakeProvider
    from app.services.scanner import StockScannerService
    from tests.fixtures.synthetic_data import SYNTHETIC_UNIVERSE

    class BrokenFrameProvider(FakeProvider):
        def download_bars(self, tickers):
            bars, count, failures = super().download_bars(tickers)
            bars["TEST1.NS"] = bars["TEST1.NS"].drop(columns=["Volume"])  # KeyError inside indicators
            return bars, count, failures

    scanner = StockScannerService(
        config=Settings(MAX_PE=20, MIN_AVG_VOLUME=0),
        provider=BrokenFrameProvider(print_banner=False),
        calendar=MarketCalendar("NSE"),
        clock=FakeClock(datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)),
    )
    results, funnel, failures, *_, is_success = scanner.run_scan(SYNTHETIC_UNIVERSE)
    assert is_success is True
    broken = [f for f in failures if f.ticker == "TEST1.NS"]
    assert broken and broken[0].code == FailureCode.UNKNOWN and broken[0].stage == Stage.INDICATORS
    assert "TEST1.NS" not in {r.ticker for r in results}
    assert funnel.failed == len(failures)

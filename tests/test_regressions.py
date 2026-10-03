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
    sm = ScanStateManager(Settings(), CircuitBreaker("d", clock=clk), CircuitBreaker("p", clock=clk), clock=clk)
    sm._loaded_from_disk_stale = {m: False for m in sm.markets}
    sm.update_scan_success("NSE", [], FunnelCounts(), [], 30.0, 500)
    clk.set_time(clk.now() + timedelta(hours=14))  # next morning, pre-open
    assert sm.evaluate_stale("NSE")[0] is False


def test_failure_list_does_not_grow_across_failed_scans():
    clk = FakeClock()
    sm = ScanStateManager(Settings(), CircuitBreaker("d", clock=clk), CircuitBreaker("p", clock=clk), clock=clk)
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

    ind = IndicatorResult(date(2026, 10, 1), 100.0, rsi=30.0, volume=1, avg_volume_20d=1.0,
                          volume_ratio=1.0, session_partial=False, rsi_trend=1.0)
    t = FunnelTracker()
    assert apply_stage1_filters(ind, Settings(), t) is False
    assert (t.filtered_rsi, t.filtered_volume, t.filtered_liquidity) == (1, 0, 0)


def test_min_avg_volume_rejection_is_counted():
    from datetime import date
    from app.core.filters import apply_stage1_filters
    from app.core.indicators import IndicatorResult
    from app.core.outcomes import FunnelTracker

    ind = IndicatorResult(date(2026, 10, 1), 100.0, rsi=60.0, volume=500, avg_volume_20d=100.0,
                          volume_ratio=5.0, session_partial=False, rsi_trend=1.0)
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
    assert sorted(f.ticker for f in fails if f.ticker != "*") == sorted(tickers)
    assert all(f.ticker != "*" for f in fails)


def test_ui_results_section_is_an_auto_refreshing_fragment():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("ui/app.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "results_fragment")
    deco = ast.unparse(fn.decorator_list[0])
    assert "fragment" in deco and "run_every" in deco
    main_src = ast.unparse(next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"))
    assert "get_results" not in main_src  # results must be fetched inside the fragment, not once per rerun

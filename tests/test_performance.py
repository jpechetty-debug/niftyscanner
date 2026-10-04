"""SYNTHETIC outcomes only: fictitious TEST tickers, prices and benchmark."""
from datetime import datetime, timedelta, timezone
import asyncio
import threading

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.cache.persistence import save_last_scan
from app.core.config import Settings
from app.main import create_app
from app.market.calendar import MarketCalendar
from app.performance.provider import YahooOutcomeProvider
from app.performance.provider import OutcomeDataUnavailable
from app.performance.repository import PerformanceRepository, strategy_context
from app.performance.service import PerformanceService, returns


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 22, 16, tzinfo=timezone.utc)

    def now(self):
        return self.value


class SyntheticPrices:
    """SYNTHETIC provider confined to tests; no production serving."""
    def __init__(self):
        self.calls = []
        self.missing = set()
        self.fail = False

    def history(self, ticker, start, end):
        self.calls.append((ticker,start,end))
        if self.fail:
            raise RuntimeError("SYNTHETIC network failure")
        cal = MarketCalendar("NSE").calendar
        result = {}
        for session in cal.sessions_in_range(start,end):
            date = session.date().isoformat()
            if (ticker,date) in self.missing:
                continue
            # Split-adjusted prices: no spurious return from a 2:1 split.
            result[date] = {"open": 50 if ticker.startswith("TEST") else 100,
                            "close": 55 if ticker.startswith("TEST") else 102,
                            "dividend": 1, "split": 2}
        return result


@pytest.fixture
def setup(tmp_path):
    cfg = Settings(_env_file=None, ENABLED_MARKETS="NSE,NYSE", PERFORMANCE_DATA_DIR=str(tmp_path))
    clock, provider = Clock(), SyntheticPrices()
    service = PerformanceService(cfg,clock,MarketCalendar("NSE"),provider)
    return cfg, clock, provider, service


def capture(setup, ticker="TEST1.NS", timestamp="2026-10-01T11:00:00+00:00", partial=False, score=.5, context=None):
    cfg = setup[0]
    payload = {"meta": {"last_refreshed": timestamp, "funnel": {}},
               "results": [{"ticker": ticker, "name": "SYNTHETIC stock", "market": "NSE",
                            "price": 9999, "pe": 10, "rsi": 60, "volume": 100,
                            "avg_volume_20d": 20, "volume_ratio": 5, "score": score,
                            "session_partial": partial, "bar_date": "2026-10-01"}],
               "strategy_context": context, "failed_symbols": []}
    save_last_scan("NSE",payload,cfg.PERFORMANCE_DATA_DIR)


def test_next_open_holidays_duplicates_legacy_and_partial(setup):
    capture(setup, timestamp="2026-10-01T12:00:00+00:00")
    capture(setup, timestamp="2026-10-01T11:00:00+00:00")
    capture(setup, partial=True)
    capture(setup, ticker="TEST2.NS", timestamp="2026-10-05T05:00:00+00:00")
    report = setup[3].report(horizon=1)
    eligible = [r for r in report["outcomes"] if not r["exclusion"]]
    assert len(eligible) == 2 and report["duplicates_removed"] == 1
    first = next(r for r in eligible if r["ticker"] == "TEST1.NS")
    assert first["entry_session"] == "2026-10-05"  # Gandhi Jayanti + weekend
    assert first["exit_session"] == "2026-10-05"
    assert "11:00:00" in first["available_at"]
    assert next(r for r in eligible if r["ticker"] == "TEST2.NS")["entry_session"] == "2026-10-06"
    assert report["summary"]["excluded"] == 1
    assert report["summary"]["unresolved"] == 2
    assert report["strategies"] == ["legacy-unknown"]
    assert setup[2].calls == []  # GET never downloads prices


def test_outcomes_returns_vintage_and_restart_idempotence(setup):
    capture(setup, context=strategy_context(setup[0]))
    assert setup[3].run_if_due()
    report = setup[3].report(horizon=5)
    row = report["outcomes"][0]
    assert row["stock_entry"] == 50  # Never use stored signal price (9999).
    assert row["stock_return"] == pytest.approx(10)
    assert row["benchmark_return"] == pytest.approx(2)
    assert row["excess_return"] == pytest.approx(8)
    assert report["summary"]["hit_rate"] == 100
    assert report["summary"]["coverage"] == 100
    assert row["vintage"] == row["evaluated_at"]
    bucket = next(b for b in report["groups"]["score"] if b["bucket"] == "0.5–<0.75")
    assert bucket["valid"] == 1 and bucket["low_sample"]
    again = PerformanceService(setup[0],setup[1],setup[3].calendar,setup[2])
    before = len(setup[2].calls)
    assert not again.run_if_due()
    assert len(setup[2].calls) == before
    with again.repository.connection() as conn:
        assert conn.execute("SELECT count(*) FROM signal_outcomes").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM performance_prices").fetchone()[0] > 0


def test_missing_exact_exit_remains_unresolved_not_next_available(setup):
    capture(setup)
    setup[2].missing.add(("TEST1.NS","2026-10-05"))
    setup[3].run_if_due()
    report = setup[3].report(horizon=1)
    assert report["summary"]["valid"] == 0
    assert report["summary"]["unresolved"] == 1
    assert report["summary"]["hit_rate"] is None
    assert "missing_exact_session_price" in report["outcomes"][0]["reason"]


def test_missing_benchmark_and_retry_budget(setup):
    capture(setup)
    setup[2].fail = True
    for _ in range(setup[0].PERFORMANCE_MAX_RETRIES):
        assert setup[3].run_if_due()
        assert not setup[3].run_if_due()
        setup[1].value += timedelta(seconds=setup[0].PERFORMANCE_RETRY_SEC+1)
    assert not setup[3].run_if_due()
    assert setup[3].report()["job"]["status"] == "failed"
    setup[2].fail = False
    setup[1].value += timedelta(days=1)
    assert setup[3].run_if_due()
    assert setup[3].report()["summary"]["valid"] == 1


def test_pending_filters_empty_buckets_and_ties(setup):
    capture(setup)
    setup[1].value = datetime(2026,10,4,12,tzinfo=timezone.utc)
    report = setup[3].report(horizon=10)
    assert report["summary"]["pending"] == 1
    assert report["summary"]["coverage"] is None
    assert all(b["hit_rate"] is None for b in report["groups"]["score"])
    assert setup[3].report(start="2026-10-02")["outcomes"] == []
    assert setup[3].report(strategy="unknown")["outcomes"] == []
    tied = {"status":"resolved", "matured":True, "excess_return":0.,"stock_return":2.,"benchmark_return":2.}
    assert setup[3].summarize([tied])["hit_rate"] == 0


@pytest.mark.parametrize("values", [(0,1,2,3),(1,float("nan"),2,3),(1,2,-1,3)])
def test_invalid_prices(values):
    with pytest.raises(ValueError):
        returns(*values)


@pytest.mark.parametrize("key,value", [("PERFORMANCE_SCORE_EDGES","0,1,1"),
    ("PERFORMANCE_RSI_EDGES","0,nan"),("PERFORMANCE_NIGHTLY_TIME","25:00"),
    ("PERFORMANCE_BATCH_SIZE",0),("PERFORMANCE_PRICE_BASIS","total_return")])
def test_performance_config_validation(key,value):
    with pytest.raises(ValueError):
        Settings(_env_file=None,**{key:value})


def test_provider_explicit_basis_split_dividend_and_end_date(monkeypatch):
    calls = []
    def download(ticker,**kwargs):
        calls.append(kwargs)
        return pd.DataFrame({"Open":[50.,50.],"Close":[55.,float("nan")],
            "Adj Close":[49.,49.],"Dividends":[1.,0.],"Stock Splits":[2.,0.]},
            index=pd.to_datetime(["2026-10-05","2026-10-06"]))
    class Ticker:
        def __init__(self,ticker):
            self.ticker = ticker
        def history(self,**kwargs):
            return download(self.ticker,**kwargs)
    monkeypatch.setattr("app.performance.provider.yf.Ticker",Ticker)
    prices = YahooOutcomeProvider().history("TEST1.NS",datetime(2026,10,5).date(),datetime(2026,10,6).date())
    assert prices["2026-10-05"]["close"] == 55
    assert "2026-10-06" not in prices
    assert calls[0]["auto_adjust"] is False and calls[0]["actions"] is True
    assert calls[0]["end"] == "2026-10-07"


def test_api_validation_and_report_thread(setup):
    capture(setup)
    app = create_app(config=setup[0],clock=setup[1],start_scheduler=False)
    app.state.performance = setup[3]
    with TestClient(app) as client:
        result = client.get("/api/performance?horizon=1")
        assert result.status_code == 200
        assert result.json()["scheduler_running"] is False
        assert result.json()["summary"]["unresolved"] == 1
        assert client.get("/api/performance?horizon=2").status_code == 422
        assert client.get("/api/performance?start=2026-10-22&end=2026-10-01").status_code == 422
        assert client.get("/api/performance?market=INVALID").status_code == 422
        assert client.get("/api/performance?market=NYSE").json()["supported"] is False


@pytest.mark.asyncio
async def test_scheduler_worker_does_not_block_event_loop(setup):
    app = create_app(config=setup[0],clock=setup[1],start_scheduler=False)
    scheduler = app.state.scheduler
    started, release = threading.Event(), threading.Event()
    class Slow:
        def run_if_due(self):
            started.set()
            release.wait(2)
    scheduler.performance = Slow()
    task = asyncio.create_task(scheduler.evaluate_performance_if_idle())
    try:
        await asyncio.to_thread(started.wait, 1)
        assert started.is_set() and not task.done()
        await asyncio.sleep(.01)
        assert scheduler.state.scan_lock.locked()
        await scheduler.evaluate_performance_if_idle()  # no overlapping job
    finally:
        release.set()
        await task


def test_transaction_rollback_and_original_rows_preserved(setup):
    capture(setup)
    repo = setup[3].repository
    setup[3].report()
    with pytest.raises(RuntimeError):
        with repo.connection() as conn:
            conn.execute("DELETE FROM signal_cohorts")
            raise RuntimeError("SYNTHETIC transaction failure")
    with repo.connection() as conn:
        assert conn.execute("SELECT count(*) FROM signal_cohorts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM signals").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM performance_schema").fetchone()[0] == 1


def test_delisted_name_does_not_starve_batches_and_catchup(setup):
    setup[0].PERFORMANCE_BATCH_SIZE = 2
    for i in range(1,5):
        capture(setup,ticker=f"TEST{i}.NS")
    original = setup[2].history
    def prices(ticker,start,end):
        if ticker == "TEST1.NS":
            raise OutcomeDataUnavailable("SYNTHETIC delisted name")
        return original(ticker,start,end)
    setup[2].history = prices
    assert setup[3].run_if_due()
    first = setup[3].report()
    assert first["summary"]["valid"] == 1
    assert first["job"]["status"] == "continuing"
    setup[1].value += timedelta(seconds=setup[0].PERFORMANCE_RETRY_SEC + 1)
    # A fresh service resumes a persisted unfinished job.
    resumed = PerformanceService(setup[0],setup[1],setup[3].calendar,setup[2])
    assert resumed.run_if_due()
    report = resumed.report()
    assert report["summary"]["valid"] == 3
    assert report["summary"]["unresolved"] == 1
    assert report["summary"]["coverage"] == 75
    assert report["job"]["status"] == "complete"


def test_benchmark_contexts_are_not_mixed(setup):
    capture(setup)
    setup[3].run_if_due()
    setup[0].PERFORMANCE_BENCHMARK = "SYNTHETIC-OTHER-INDEX"
    report = setup[3].report()
    assert report["summary"]["valid"] == 0
    assert report["outcomes"][0]["benchmark"] == "SYNTHETIC-OTHER-INDEX"
    with setup[3].repository.connection() as conn:
        assert conn.execute("SELECT count(*) FROM signal_outcomes WHERE status='resolved'").fetchone()[0] == 3


def test_morning_catchup_retries_again_at_next_nightly_deadline(setup):
    capture(setup)
    setup[1].value = datetime(2026,10,22,14,tzinfo=timezone.utc)  # before 21:00 IST
    setup[2].missing.add(("TEST1.NS","2026-10-05"))
    setup[3].run_if_due()
    assert setup[3].report()["summary"]["valid"] == 0
    setup[2].missing.clear()
    setup[1].value = datetime(2026,10,22,16,tzinfo=timezone.utc)
    assert setup[3].run_if_due()
    assert setup[3].report()["summary"]["valid"] == 1


def test_performance_ui_controls_and_export(setup, monkeypatch):
    import importlib
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    capture(setup)
    setup[3].run_if_due()
    monkeypatch.syspath_prepend(str(Path("ui").resolve()))
    client = importlib.import_module("api_client").ScreenerApiClient
    calls = []
    def performance(self,**kwargs):
        calls.append(kwargs)
        normalized = {k: v.isoformat() if hasattr(v,"isoformat") else v for k,v in kwargs.items()}
        return setup[3].report(**normalized), None
    monkeypatch.setattr(client,"get_performance",performance)
    page = AppTest.from_string("from performance import render_performance\nfrom api_client import ScreenerApiClient\nrender_performance(ScreenerApiClient(), 'NSE')").run()
    assert not page.exception
    assert page.metric[0].value == "100.00%"
    assert any("Export outcomes CSV" in button.label for button in page.get("download_button"))
    page.selectbox(key="perf_horizon_NSE").select(10).run()
    page.radio(key="perf_group_NSE").set_value("RSI band").run()
    assert not page.exception and calls[-1]["horizon"] == 10
    assert any(page.dataframe[0].value["bucket"] == "60–<70")
    page.date_input(key="perf_start_NSE").set_value(datetime(2026,10,23).date()).run()
    assert not page.exception
    assert "No evaluated outcomes yet" in page.info[0].value

"""SYNTHETIC prices/reference values; exchange-date checks use published calendar facts."""
from datetime import datetime, timezone

import exchange_calendars as xcals
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.interfaces import ScanResultItem
from app.main import create_app
from app.market.calendar import MarketCalendar
from app.performance.costs import net_return
from app.providers.nse_reference import CSVReferenceProvider, enrich_result
from tests.test_performance import setup, capture


def synthetic_item(**kwargs):
    return ScanResultItem(ticker="TEST1.NS", name="SYNTHETIC", market="NSE", price=105,
                         pe=20, rsi=60, volume=200, avg_volume_20d=100, volume_ratio=2,
                         score=.5, session_partial=False, bar_date="2026-10-01", **kwargs)


def test_nse_election_closure_and_global_proxy_unchanged(setup):
    cal = MarketCalendar("NSE", setup[0])
    assert not cal.calendar.is_session("2026-01-15")
    assert xcals.get_calendar("XBOM").is_session("2026-01-15")
    assert MarketCalendar("NYSE", setup[0]).calendar.is_session("2026-01-15")
    assert cal.get_last_expected_session(datetime(2026, 1, 15, 10, tzinfo=timezone.utc)).isoformat() == "2026-01-14"
    row = {"bar_date": "2026-01-14", "timestamp": "2026-01-14T11:00:00+00:00", "session_partial": 0}
    schedule = setup[3]._capture_schedule(row)
    assert schedule[2] == "2026-01-16"
    assert setup[3]._exits("2026-01-16")[1][0] == "2026-01-16"


def test_override_can_be_disabled():
    assert MarketCalendar("NSE", Settings(_env_file=None, NSE_EXTRA_HOLIDAYS="")).calendar.is_session("2026-01-15")


@pytest.mark.parametrize("key,value", [("NSE_EXTRA_HOLIDAYS", "bad"), ("CIRCUIT_BANDS_PCT", "2,nan"),
    ("CIRCUIT_BANDS_PCT", "0"), ("PERFORMANCE_SLIPPAGE_BPS", -1),
    ("PERFORMANCE_OTHER_COST_BPS", float("inf"))])
def test_new_config_validation(key, value):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{key: value})


def test_missing_reference_does_not_claim_safe_or_verified():
    cfg = Settings(_env_file=None)
    row = synthetic_item(day_change_pct=4.9)
    enrich_result(row, cfg, {})
    assert row.circuit_risk is True and row.circuit_risk_status == "indicative"
    assert row.pe_check_status == "unavailable"
    normal = synthetic_item(day_change_pct=1.1)
    enrich_result(normal, cfg, {})
    assert normal.circuit_risk is None and normal.circuit_risk_status == "unavailable"
    assert row.score == .5


def test_dated_reference_band_pe_mismatch_and_fno(tmp_path):
    path = tmp_path / "synthetic_reference.csv"
    path.write_text("Symbol,Date,Source,PE,PriceBandPct,FNO\nTEST1,2026-10-01,SYNTHETIC independent source,10,5,no\n")
    references = CSVReferenceProvider(str(path)).load()
    cfg = Settings(_env_file=None)
    row = synthetic_item(day_change_pct=-4.9)
    enrich_result(row, cfg, references)
    assert row.circuit_risk is True and row.circuit_risk_status == "reference_band"
    assert row.pe_check_status == "divergent" and row.pe_reference == 10
    references[("TEST1", "2026-10-01")]["FNO"] = "yes"
    dynamic = synthetic_item(day_change_pct=10)
    enrich_result(dynamic, cfg, references)
    assert dynamic.circuit_risk is None and dynamic.circuit_risk_status == "dynamic_band"
    assert dynamic.pe_check_status == "divergent"
    path.write_text("Symbol,Date,Source,PE,PriceBandPct,FNO\nTEST1,2026-09-30,SYNTHETIC,20,5,no\n")
    stale = synthetic_item(day_change_pct=1)
    enrich_result(stale, cfg, CSVReferenceProvider(str(path)).load())
    assert stale.reference_date is None and stale.pe_check_status == "unavailable"


def test_bad_duplicate_or_missing_reference_is_unavailable(tmp_path):
    assert CSVReferenceProvider(str(tmp_path / "absent.csv")).load() == {}
    path = tmp_path / "synthetic.csv"
    path.write_text("Symbol,Date,Source\nTEST1,2026-10-01,SYNTHETIC\nTEST1,2026-10-01,SYNTHETIC\n")
    assert CSVReferenceProvider(str(path)).load() == {}
    path.write_text("WrongHeader\nTEST1\n")
    assert CSVReferenceProvider(str(path)).load() == {}


def test_net_return_cash_flows_and_zero_costs():
    cfg = Settings(_env_file=None)
    expected = 100 * ((100 * .9995 * .9989) / (100 * 1.0005 * 1.00125) - 1)
    assert net_return(100, 100, cfg) == pytest.approx(expected)
    assert net_return(100, 100, cfg) < 0
    zero = cfg.model_copy(update={key: 0 for key in cfg.model_fields if key.endswith("_BPS")})
    assert net_return(100, 110, zero) == pytest.approx(10)
    assert net_return(100, 90, cfg) < -10


def test_net_grouping_and_gross_database_preserved(setup):
    capture(setup)
    setup[3].run_if_due()
    with setup[3].repository.connection() as conn:
        conn.execute("UPDATE signal_outcomes SET stock_exit=50.025,stock_return=.05,benchmark_return=0,excess_return=.05 WHERE horizon=5")
    gross = setup[3].report(horizon=5)
    net = setup[3].report(horizon=5, return_basis="net")
    assert gross["summary"]["hit_rate"] == 100
    assert net["summary"]["hit_rate"] == 0
    assert next(b for b in net["groups"]["score"] if b["valid"])["hit_rate"] == 0
    assert net["outcomes"][0]["gross_excess_return"] == .05
    assert net["outcomes"][0]["estimated_cost_drag_pp"] > 0
    with setup[3].repository.connection() as conn:
        assert conn.execute("SELECT stock_return FROM signal_outcomes WHERE horizon=5").fetchone()[0] == .05
    assert setup[3].report(horizon=5)["summary"] == gross["summary"]


def test_net_pending_and_api_validation(setup):
    capture(setup)
    setup[1].value = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
    app = create_app(config=setup[0], clock=setup[1], start_scheduler=False)
    app.state.performance = setup[3]
    with TestClient(app) as client:
        response = client.get("/api/performance?return_basis=net")
        assert response.status_code == 200
        data = response.json()
        assert data["return_basis"] == "net" and data["summary"]["pending"] == 1
        assert data["summary"]["mean_stock"] is None
        assert "net_stock_return" not in data["outcomes"][0]
        assert data["cost_model"]["buy_stt_bps"] == 10
        assert client.get("/api/performance?return_basis=invalid").status_code == 422


def test_scanner_context_flows_to_json_api_and_csv(tmp_path):
    import asyncio
    import csv
    import io
    import pandas as pd
    from app.core.interfaces import UniverseSymbol
    from app.market.clock import FakeClock
    from app.services.scanner import StockScannerService
    from app.cache.persistence import save_last_scan, load_last_scan_on_startup
    cfg = Settings(_env_file=None, ENABLED_MARKETS="NSE", PERFORMANCE_DATA_DIR=str(tmp_path),
                   MIN_AVG_TRADED_VALUE_NSE=0, MIN_RSI=0, RSI_CAP=100, REQUIRE_RSI_TREND_UP=False)
    class SyntheticProvider:
        def download_bars(self, tickers):
            closes = [100 + (i % 3) for i in range(60)]
            closes[-1] = closes[-2] * 1.049
            return {"TEST1.NS": pd.DataFrame({"Close": closes, "Volume": [100]*59+[200]},
                index=pd.date_range(end="2026-10-01", periods=60, freq="B"))}, 1, []
        def fetch_pe_batch(self, tickers):
            return {"TEST1.NS": 20}, 1, []
    class SyntheticReferences:
        def load(self):
            return {("TEST1", "2026-10-01"): {"Source": "SYNTHETIC", "PE": 20, "FNO": "no", "PriceBandPct": 5}}
    clock = FakeClock(datetime(2026, 10, 1, 11, tzinfo=timezone.utc))
    provider = SyntheticProvider()
    scanner = StockScannerService(cfg, provider, clock=clock, reference_provider=SyntheticReferences())
    symbols = [UniverseSymbol(symbol="TEST1", ticker="TEST1.NS", company_name="SYNTHETIC", market="NSE")]
    class SyntheticUniverse:
        def load(self):
            return symbols
    results, counts, failures, requests, _, ok = scanner.run_scan(symbols)
    assert ok and counts.passed_pe == 1 and not failures and requests == 2
    assert results[0].day_change_pct == pytest.approx(4.9)
    assert results[0].circuit_risk is True and results[0].pe_check_status == "match"
    payload = {"meta": {"last_refreshed": clock.now().isoformat(), "funnel": counts.model_dump()},
               "results": [item.model_dump() for item in results], "failed_symbols": []}
    save_last_scan("NSE", payload, str(tmp_path))
    assert load_last_scan_on_startup("NSE", str(tmp_path))["results"][0]["circuit_risk"] is True
    app = create_app(config=cfg, clock=clock, provider=provider,
                     universe_loader=SyntheticUniverse(), start_scheduler=False)
    asyncio.run(app.state.scheduler.execute_scan("NSE"))
    with TestClient(app) as client:
        result = client.get("/api/results").json()["results"][0]
        assert result["day_change_pct"] == pytest.approx(4.9)
        assert result["circuit_risk_status"] == "indicative"
        row = next(csv.DictReader(io.StringIO(client.get("/api/export.csv").text)))
        assert row["circuit_risk_status"] == "indicative" and float(row["day_change_pct"]) == pytest.approx(4.9)

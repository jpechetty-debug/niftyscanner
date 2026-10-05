"""Score breakdown and score-band track record (spec section 25). SYNTHETIC data only."""

import math
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.ranking import calculate_composite_score, score_components
from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock
from app.providers.fake_provider import FakeProvider
from app.services.scanner import StockScannerService
from tests.fixtures.synthetic_data import SYNTHETIC_UNIVERSE
from tests.test_performance import capture, setup  # noqa: F401  (fixture reuse)


def test_components_sum_to_composite_score():
    cfg = Settings()
    for args in ((2.0, 55.0, 12.0), (12.0, 90.0, 5.0), (1.6, 41.0, 49.0)):
        parts = score_components(*args, cfg)
        assert math.isclose(sum(parts), calculate_composite_score(*args, cfg), abs_tol=1e-12)
        assert all(0 <= p <= w for p, w in zip(parts, (cfg.WEIGHT_VOLUME, cfg.WEIGHT_RSI, cfg.WEIGHT_PE)))


def test_scanner_results_carry_components():
    scanner = StockScannerService(config=Settings(MAX_PE=20, MIN_AVG_VOLUME=0),
                                  provider=FakeProvider(print_banner=False), calendar=MarketCalendar("NSE"),
                                  clock=FakeClock(datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)))
    results, *_ = scanner.run_scan(SYNTHETIC_UNIVERSE)
    assert results
    for r in results:
        assert math.isclose(r.score_volume + r.score_rsi + r.score_pe, r.score, abs_tol=1e-12)


def test_score_track_record_buckets(setup):  # noqa: F811
    cfg, clock, provider, service = setup
    capture(setup, ticker="TEST1.NS", score=0.6)
    capture(setup, ticker="TEST2.NS", score=0.3)
    empty = service.score_track_record(horizon=1)
    assert all(b["valid"] == 0 and b["hit_rate"] is None for b in empty["buckets"])

    service.report(horizon=1)  # ingest captures
    service.run_if_due()       # resolve with SYNTHETIC prices: stock +10%, index +2%
    buckets = service.score_track_record(horizon=1)["buckets"]
    by_band = {(b["low"], b["high"]): b for b in buckets}
    assert by_band[(0.5, 0.75)]["valid"] == 1 and by_band[(0.5, 0.75)]["hit_rate"] == 100
    assert by_band[(0.25, 0.5)]["valid"] == 1 and by_band[(0.5, 0.75)]["low_sample"] is True
    assert by_band[(None, 0.0)]["valid"] == 0 and by_band[(1.0, None)]["valid"] == 0


def test_score_buckets_endpoint_validation():
    from app.main import create_app
    from app.universe.nse import NSEUniverse
    app = create_app(config=Settings(ENABLED_MARKETS="NSE"), provider=FakeProvider(print_banner=False),
                     universe_loader=NSEUniverse("tests/fixtures/nifty500_synthetic.csv"),
                     clock=FakeClock(), calendar=MarketCalendar("NSE"), start_scheduler=False)
    app.state.performance.score_track_record = lambda horizon: {"supported": True, "horizon": horizon, "buckets": []}
    client = TestClient(app)
    assert client.get("/api/performance/score-buckets?horizon=5").json()["horizon"] == 5
    assert client.get("/api/performance/score-buckets?horizon=3").status_code == 422
    assert client.get("/api/performance/score-buckets?market=NYSE").status_code == 404
    assert client.get("/api/performance/score-buckets?market=XYZ").status_code == 422


def test_ui_labels():
    from ui.app import score_mix_label, track_record_label
    buckets = [{"low": None, "high": 0.5, "valid": 0, "hit_rate": None, "low_sample": True},
               {"low": 0.5, "high": None, "valid": 41, "hit_rate": 58.5, "low_sample": False}]
    assert track_record_label(0.7, buckets) == "58% beat index · n=41"
    assert track_record_label(0.2, buckets) == "No history"
    assert track_record_label(0.7, None) == "No history"
    small = [{**buckets[1], "valid": 3, "low_sample": True}]
    assert track_record_label(0.9, small).endswith("(low sample)")
    assert score_mix_label({"score_volume": 0.32, "score_rsi": 0.1, "score_pe": 0.08}) == "Vol 0.32 · RSI 0.10 · P/E 0.08"
    assert score_mix_label({"score_volume": None}) == "Unavailable"

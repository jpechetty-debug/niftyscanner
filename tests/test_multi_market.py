"""Multi-market integration tests for simultaneous NSE and NYSE support."""

from datetime import datetime, timezone
from fastapi.testclient import TestClient
import pytest

from app.core.config import Settings
from app.main import create_app
from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock
from app.providers.fake_provider import FakeProvider
from app.universe.nse import NSEUniverse
from app.universe.nyse import NYSEUniverse


@pytest.fixture
def multi_market_client():
    """Create test FastAPI client configured with both NSE and NYSE enabled."""
    config = Settings(
        ENABLED_MARKETS="NSE,NYSE",
        REFRESH_INTERVAL_SEC=60,
        REFRESH_COOLDOWN_SEC=30,
    )
    clock = FakeClock(datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc))

    calendars = {
        "NSE": MarketCalendar("NSE"),
        "NYSE": MarketCalendar("NYSE"),
    }
    universes = {
        "NSE": NSEUniverse("tests/fixtures/nifty500_synthetic.csv"),
        "NYSE": NYSEUniverse("tests/fixtures/otherlisted_synthetic.txt", config=config),
    }
    provider = FakeProvider(print_banner=False)

    app = create_app(
        config=config,
        provider=provider,
        universe_loader=universes,
        calendar=calendars,
        clock=clock,
        start_scheduler=False,
    )
    client = TestClient(app)
    return app, client, clock


def test_enabled_markets_list_property():
    """Verify ENABLED_MARKETS parsing into uppercase list."""
    s = Settings(ENABLED_MARKETS="nse, nyse")
    assert s.enabled_markets_list == ["NSE", "NYSE"]


def test_api_multi_market_endpoints(multi_market_client):
    """Verify both NSE and NYSE return 200, refresh accepted, and CSV export works."""
    app, client, clock = multi_market_client

    # 1. GET /api/results for NSE -> 200
    res_nse = client.get("/api/results?market=NSE")
    assert res_nse.status_code == 200
    assert res_nse.json()["meta"]["market"] == "NSE"

    # 2. GET /api/results for NYSE -> 200 (Enabled in Phase 4)
    res_nyse = client.get("/api/results?market=NYSE")
    assert res_nyse.status_code == 200
    assert res_nyse.json()["meta"]["market"] == "NYSE"

    # 3. POST /api/refresh for NYSE -> 202
    res_ref = client.post("/api/refresh?market=NYSE")
    assert res_ref.status_code == 202
    assert res_ref.json()["market"] == "NYSE"

    # 4. GET /api/export.csv for NYSE -> 200
    res_csv = client.get("/api/export.csv?market=NYSE")
    assert res_csv.status_code == 200
    assert "screener_NYSE_" in res_csv.headers["content-disposition"]


def test_api_disabled_market_handling():
    """Verify market disabled when not in ENABLED_MARKETS returns 404."""
    config_nse_only = Settings(ENABLED_MARKETS="NSE")
    clock = FakeClock()
    calendars = {"NSE": MarketCalendar("NSE")}
    universes = {"NSE": NSEUniverse("tests/fixtures/nifty500_synthetic.csv")}
    app = create_app(
        config=config_nse_only,
        provider=FakeProvider(print_banner=False),
        universe_loader=universes,
        calendar=calendars,
        clock=clock,
        start_scheduler=False,
    )
    client = TestClient(app)

    res_nyse = client.get("/api/results?market=NYSE")
    assert res_nyse.status_code == 404

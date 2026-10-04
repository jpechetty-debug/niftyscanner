"""API integration tests using FastAPI TestClient covering all status codes and endpoints."""

import asyncio
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock
from app.providers.fake_provider import FakeProvider
from app.universe.nse import NSEUniverse


@pytest.fixture
def app_and_client():
    """Create test application wired to FakeProvider and synthetic universe."""
    config = Settings(
        REFRESH_INTERVAL_SEC=60,
        REFRESH_COOLDOWN_SEC=30,
        ENABLED_MARKETS="NSE",
    )
    clock = FakeClock(datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc))
    calendar = MarketCalendar("NSE")
    provider = FakeProvider(print_banner=False)
    universe = NSEUniverse("tests/fixtures/nifty500_synthetic.csv")

    app = create_app(
        config=config,
        provider=provider,
        universe_loader=universe,
        clock=clock,
        calendar=calendar,
        start_scheduler=False,  # Control scans directly in tests
    )

    client = TestClient(app)
    return app, client, clock


def test_get_results_endpoints_and_status_codes(app_and_client):
    """Test /api/results market validation: 200 (NSE), 404 (disabled NYSE), 422 (unknown)."""
    app, client, clock = app_and_client

    # 1. Valid enabled market (NSE)
    res_nse = client.get("/api/results?market=NSE")
    assert res_nse.status_code == 200
    data = res_nse.json()
    assert "meta" in data
    assert "results" in data
    assert "failed_symbols" in data
    assert data["meta"]["market"] == "NSE"

    # 2. Known but disabled market (NYSE) -> 404
    res_nyse = client.get("/api/results?market=NYSE")
    assert res_nyse.status_code == 404
    assert "disabled" in res_nyse.json()["detail"].lower()

    # 3. Unknown market -> 422
    res_unknown = client.get("/api/results?market=UNKNOWN")
    assert res_unknown.status_code == 422


def test_get_status_endpoint(app_and_client):
    """Test /api/status returns operational metrics and breaker states."""
    app, client, clock = app_and_client

    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert "market_status" in data
    assert "download_breaker" in data
    assert "pe_breaker" in data
    assert "stale" in data
    assert data["download_breaker"] == "CLOSED"


def test_refresh_cooldown_and_429(app_and_client):
    """Test /api/refresh returns 202 when permitted and 429 during cooldown with Retry-After header."""
    app, client, clock = app_and_client

    # Initial refresh -> 202 Accepted
    res1 = client.post("/api/refresh?market=NSE")
    assert res1.status_code == 202
    assert "Scan initiated" in res1.json()["message"]

    # Immediate second refresh -> 429 Too Many Requests
    res2 = client.post("/api/refresh?market=NSE")
    assert res2.status_code == 429
    assert "Retry-After" in res2.headers
    retry_after = int(res2.headers["Retry-After"])
    assert retry_after > 0


def test_export_csv_endpoint(app_and_client):
    """Test /api/export.csv returns CSV with valid headers and data."""
    app, client, clock = app_and_client

    res = client.get("/api/export.csv?market=NSE")
    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    assert "attachment" in res.headers["content-disposition"]
    lines = res.text.strip().split("\r\n") if "\r\n" in res.text else res.text.strip().split("\n")
    header = lines[0]
    assert "ticker" in header
    assert "volume_ratio" in header
    assert "score" in header


def test_settings_get_and_put(app_and_client):
    """Test /api/settings GET and PUT with bounds validation [30, 300]."""
    app, client, clock = app_and_client

    # 1. GET current settings
    res_get = client.get("/api/settings")
    assert res_get.status_code == 200
    assert res_get.json()["refresh_interval_sec"] == 60

    # 2. PUT out-of-range (< 30) -> 422
    res_low = client.put("/api/settings", json={"refresh_interval_sec": 20})
    assert res_low.status_code == 422

    # 3. PUT out-of-range (> 300) -> 422
    res_high = client.put("/api/settings", json={"refresh_interval_sec": 350})
    assert res_high.status_code == 422

    # 4. PUT valid value -> 200, updates immediately
    res_ok = client.put("/api/settings", json={"refresh_interval_sec": 120})
    assert res_ok.status_code == 200
    assert res_ok.json()["refresh_interval_sec"] == 120

    # Verify updated in subsequent GET
    res_get2 = client.get("/api/settings")
    assert res_get2.json()["refresh_interval_sec"] == 120


# Actual worker-thread responsiveness and shutdown are covered in test_repairs.py.

"""Tests for Macro Market Indicators (India VIX, Brent Crude, Nifty 50 / GIFT Proxy)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.macro.models import MacroIndicatorItem, MacroOverviewResponse
from app.macro.provider import FakeMacroProvider, LiveMacroProvider
from app.main import create_app
from app.market.clock import FakeClock
from app.providers.fake_provider import FakeProvider
from app.universe.nse import NSEUniverse
from ui.api_client import ScreenerApiClient


def test_macro_models():
    """Verify validation and serialization of macro models."""
    item = MacroIndicatorItem(
        symbol="^INDIAVIX",
        name="India VIX",
        current=14.2,
        prev_close=13.8,
        change=0.4,
        change_pct=2.9,
        unit="",
        sentiment="Moderate Volatility",
        tone="neutral",
    )
    assert item.symbol == "^INDIAVIX"
    assert item.change == 0.4
    assert not item.synthetic

    overview = MacroOverviewResponse(
        indicators=[item],
        market_regime="Balanced · Stable Macro Environment",
        regime_tone="neutral",
        last_refreshed="2026-10-22T09:00:00Z",
        synthetic=False,
    )
    assert len(overview.indicators) == 1
    assert overview.market_regime.startswith("Balanced")


def test_fake_macro_provider():
    """Verify FakeMacroProvider returns synthetic indicators with proper flags."""
    clock = FakeClock(datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc))
    provider = FakeMacroProvider(clock=clock)
    data = provider.get_macro_data()

    assert data.synthetic is True
    assert len(data.indicators) == 3
    symbols = [ind.symbol for ind in data.indicators]
    assert "^NSEI" in symbols
    assert "^INDIAVIX" in symbols
    assert "BZ=F" in symbols
    for ind in data.indicators:
        assert ind.synthetic is True
        assert ind.current > 0


def test_live_macro_provider_caching():
    """Verify LiveMacroProvider caches responses within MACRO_CACHE_TTL_SEC."""
    config = Settings(
        MACRO_CACHE_TTL_SEC=60,
        MACRO_ENABLED=True,
    )
    clock = FakeClock(datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc))
    provider = LiveMacroProvider(config=config, clock=clock)

    mock_resp = MacroOverviewResponse(
        indicators=[],
        market_regime="Mocked Regime",
        regime_tone="neutral",
        last_refreshed=clock.now().isoformat(),
    )

    with patch.object(provider, "_fetch_live_data", return_value=mock_resp) as mock_fetch:
        # First call fetches live data
        res1 = provider.get_macro_data()
        assert res1.market_regime == "Mocked Regime"
        assert mock_fetch.call_count == 1

        # Second call immediately after should return cached response without calling _fetch_live_data
        res2 = provider.get_macro_data()
        assert res2.market_regime == "Mocked Regime"
        assert mock_fetch.call_count == 1


def test_live_macro_provider_sentiment_calculation():
    """Verify sentiment and regime determination logic."""
    config = Settings(
        MACRO_VIX_LOW_THRESHOLD=14.0,
        MACRO_VIX_HIGH_THRESHOLD=20.0,
    )
    clock = FakeClock(datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc))
    provider = LiveMacroProvider(config=config, clock=clock)

    # Mock yf.download to return controlled prices
    import pandas as pd
    dates = pd.date_range("2026-10-20", periods=2)
    close_data = {
        "^NSEI": [22000.0, 22220.0],  # +1.0% bullish
        "^INDIAVIX": [15.0, 13.0],    # 13.0 < 14 (low volatility)
        "BZ=F": [80.0, 77.0],         # -3.75% softening
    }
    df = pd.DataFrame(close_data, index=dates)
    df.columns = pd.MultiIndex.from_tuples([("Close", col) for col in close_data.keys()])

    with patch("yfinance.download", return_value=df):
        overview = provider.get_macro_data()
        assert len(overview.indicators) == 3

        ind_map = {ind.symbol: ind for ind in overview.indicators}

        # Nifty check
        nifty = ind_map["^NSEI"]
        assert nifty.change_pct == 1.0
        assert nifty.tone == "positive"
        assert "Bullish" in nifty.sentiment

        # VIX check
        vix = ind_map["^INDIAVIX"]
        assert vix.current == 13.0
        assert vix.tone == "positive"
        assert "Low Volatility" in vix.sentiment

        # Brent check
        brent = ind_map["BZ=F"]
        assert brent.tone == "positive"
        assert "Softening" in brent.sentiment

        # Overall regime
        assert "Risk-On" in overview.market_regime
        assert overview.regime_tone == "positive"


def test_macro_api_endpoint():
    """Test GET /api/macro endpoint using FastAPI TestClient with FakeMacroProvider."""
    config = Settings(ENABLED_MARKETS="NSE", MACRO_ENABLED=True)
    clock = FakeClock(datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc))
    provider = FakeProvider(print_banner=False)
    macro_prov = FakeMacroProvider(clock=clock)
    universe = NSEUniverse("tests/fixtures/nifty500_synthetic.csv")

    app = create_app(
        config=config,
        provider=provider,
        macro_provider=macro_prov,
        universe_loader=universe,
        clock=clock,
        start_scheduler=False,
    )
    client = TestClient(app)

    res = client.get("/api/macro")
    assert res.status_code == 200
    data = res.json()
    assert "indicators" in data
    assert len(data["indicators"]) == 3
    assert data["synthetic"] is True
    assert "market_regime" in data


def test_macro_api_disabled():
    """Test GET /api/macro when macro service is disabled."""
    config = Settings(ENABLED_MARKETS="NSE", MACRO_ENABLED=False)
    clock = FakeClock(datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc))
    provider = FakeProvider(print_banner=False)
    universe = NSEUniverse("tests/fixtures/nifty500_synthetic.csv")

    app = create_app(
        config=config,
        provider=provider,
        universe_loader=universe,
        clock=clock,
        start_scheduler=False,
    )
    client = TestClient(app)

    res = client.get("/api/macro")
    assert res.status_code == 503
    assert "disabled" in res.json()["detail"].lower()


def test_ui_api_client_macro():
    """Test ScreenerApiClient.get_macro_overview method."""
    client = ScreenerApiClient(base_url="http://127.0.0.1:8000")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "indicators": [
            {"symbol": "^INDIAVIX", "name": "India VIX", "current": 15.0}
        ],
        "market_regime": "Balanced",
    }

    with patch("httpx.Client.get", return_value=mock_resp):
        data, err = client.get_macro_overview()
        assert err is None
        assert data is not None
        assert data["market_regime"] == "Balanced"
        assert len(data["indicators"]) == 1

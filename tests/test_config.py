"""Unit tests for configuration loading and validation."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, load_settings


def test_default_config_valid():
    """Default settings must be valid and adhere to all spec boundaries."""
    settings = Settings()
    assert settings.API_HOST == "127.0.0.1"
    assert settings.REFRESH_INTERVAL_SEC == 60
    assert settings.MIN_RSI == 50.0
    assert settings.RSI_CAP == 80.0
    assert settings.MIN_VOLUME_RATIO == 2.0
    assert settings.VOLUME_RATIO_CAP == 10.0
    assert settings.MAX_PE == 20.0
    assert settings.MIN_PE == 0.0
    assert settings.MIN_BARS == 60
    assert settings.VOLUME_LOOKBACK == 20


def test_weights_sum_validation():
    """Weights must sum to 1.0 within 1e-6 tolerance."""
    with pytest.raises(ValidationError, match="must sum to 1.0"):
        Settings(WEIGHT_VOLUME=0.5, WEIGHT_RSI=0.5, WEIGHT_PE=0.1)


def test_refresh_interval_bounds():
    """REFRESH_INTERVAL_SEC must be within 30 to 300."""
    with pytest.raises(ValidationError, match="REFRESH_INTERVAL_SEC"):
        Settings(REFRESH_INTERVAL_SEC=29)

    with pytest.raises(ValidationError, match="REFRESH_INTERVAL_SEC"):
        Settings(REFRESH_INTERVAL_SEC=301)

    s1 = Settings(REFRESH_INTERVAL_SEC=30)
    assert s1.REFRESH_INTERVAL_SEC == 30
    s2 = Settings(REFRESH_INTERVAL_SEC=300)
    assert s2.REFRESH_INTERVAL_SEC == 300


def test_rsi_cap_greater_than_min():
    """RSI_CAP must be strictly greater than MIN_RSI."""
    with pytest.raises(ValidationError, match="RSI_CAP .* strictly greater than MIN_RSI"):
        Settings(MIN_RSI=50, RSI_CAP=50)


def test_volume_ratio_cap_greater_than_min():
    """VOLUME_RATIO_CAP must be strictly greater than MIN_VOLUME_RATIO."""
    with pytest.raises(ValidationError, match="VOLUME_RATIO_CAP .* strictly greater than MIN_VOLUME_RATIO"):
        Settings(MIN_VOLUME_RATIO=2.0, VOLUME_RATIO_CAP=2.0)


def test_pe_bounds_validation():
    """MAX_PE > MIN_PE >= 0."""
    with pytest.raises(ValidationError, match="MAX_PE > MIN_PE >= 0"):
        Settings(MAX_PE=10.0, MIN_PE=15.0)

    with pytest.raises(ValidationError, match="MAX_PE > MIN_PE >= 0"):
        Settings(MAX_PE=10.0, MIN_PE=-1.0)


def test_min_bars_greater_than_lookback():
    """MIN_BARS must be >= VOLUME_LOOKBACK + 1."""
    with pytest.raises(ValidationError, match="MIN_BARS .* must be >= VOLUME_LOOKBACK"):
        Settings(MIN_BARS=20, VOLUME_LOOKBACK=20)

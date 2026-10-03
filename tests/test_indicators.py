"""Unit tests for data hygiene, Wilder RSI, and volume indicators."""

from datetime import date, datetime, timedelta, timezone
import math
import numpy as np
import pandas as pd
import pytest

from app.core.indicators import (
    clean_and_validate_bars,
    compute_indicators,
    compute_volume_metrics,
    compute_wilder_rsi,
)
from app.core.outcomes import FailureCode
from app.market.calendar import MarketCalendar


def independent_wilder_rsi(prices: pd.Series, period: int = 14) -> float:
    """Independent reference implementation of Wilder's RSI using loop smoothing."""
    delta = prices.diff().dropna()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)

    # Wilder smoothing with adjust=False seeds from initial value
    alpha = 1.0 / period
    avg_gain = gains.iloc[0]
    avg_loss = losses.iloc[0]

    for i in range(1, len(delta)):
        avg_gain = alpha * gains.iloc[i] + (1.0 - alpha) * avg_gain
        avg_loss = alpha * losses.iloc[i] + (1.0 - alpha) * avg_loss

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def test_rsi_matches_independent_reference_long_series():
    """RSI calculation on >= 100 bars must match independent reference implementation."""
    np.random.seed(42)
    n = 120
    # Simulate realistic random walk prices
    returns = np.random.normal(0.001, 0.02, n)
    prices = pd.Series(100.0 * np.exp(np.cumsum(returns)))

    rsi_calc, code, msg = compute_wilder_rsi(prices, period=14)
    assert code is None
    assert rsi_calc is not None

    rsi_ref = independent_wilder_rsi(prices, period=14)
    assert math.isclose(rsi_calc, rsi_ref, abs_tol=1e-5)


def test_rsi_flat_series_and_ordering():
    """Flat series must return FLAT_SERIES and take precedence before avg_loss == 0 check."""
    # Series with no price movement
    flat_prices = pd.Series([100.0] * 50)
    rsi_val, code, msg = compute_wilder_rsi(flat_prices, period=14)
    assert rsi_val is None
    assert code == FailureCode.FLAT_SERIES

    # Monotonically increasing series: avg_loss == 0 -> RSI = 100.0
    rising_prices = pd.Series([100.0 + i * 2.0 for i in range(50)])
    rsi_val, code, msg = compute_wilder_rsi(rising_prices, period=14)
    assert code is None
    assert rsi_val == 100.0


def test_volume_metrics_edge_cases():
    """Test volume edge cases per Section 9 and 20."""
    lookback = 20

    # 1. Normal valid case
    vols = [100_000.0] * lookback + [250_000.0]
    s_valid = pd.Series(vols)
    cur_vol, avg20, ratio, code, msg = compute_volume_metrics(s_valid, lookback=lookback)
    assert code is None
    assert cur_vol == 250_000
    assert avg20 == 100_000.0
    assert ratio == 2.5

    # 2. NaN on latest bar -> MISSING_VOLUME (must not fall back to older bar!)
    vols_nan_latest = [100_000.0] * lookback + [np.nan]
    _, _, _, code, msg = compute_volume_metrics(pd.Series(vols_nan_latest), lookback=lookback)
    assert code == FailureCode.MISSING_VOLUME

    # 3. Zero on latest bar -> INVALID_VOLUME
    vols_zero_latest = [100_000.0] * lookback + [0]
    _, _, _, code, msg = compute_volume_metrics(pd.Series(vols_zero_latest), lookback=lookback)
    assert code == FailureCode.INVALID_VOLUME

    # 4. NaN in 20-bar lookback window -> MISSING_VOLUME
    vols_nan_window = [100_000.0] * 10 + [np.nan] + [100_000.0] * 9 + [200_000.0]
    _, _, _, code, msg = compute_volume_metrics(pd.Series(vols_nan_window), lookback=lookback)
    assert code == FailureCode.MISSING_VOLUME

    # 5. avg20 == 0 -> INVALID_VOLUME
    vols_zero_window = [0.0] * lookback + [100_000.0]
    _, _, _, code, msg = compute_volume_metrics(pd.Series(vols_zero_window), lookback=lookback)
    assert code == FailureCode.INVALID_VOLUME


def test_data_hygiene_dedupe_and_nan_close():
    """Verify deduplication by date (keep last) and Close NaN dropping only."""
    calendar = MarketCalendar("NSE")
    current_dt = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)

    dates = pd.date_range(end="2026-10-01", periods=65, freq="B")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 65,
            "High": [105.0] * 65,
            "Low": [95.0] * 65,
            "Close": [100.0] * 65,
            "Volume": [100_000.0] * 65,
        },
        index=dates,
    )

    # Insert a duplicate index entry with updated close
    dup_row = pd.DataFrame(
        {"Open": [100.0], "High": [106.0], "Low": [96.0], "Close": [102.0], "Volume": [150_000.0]},
        index=[dates[-1]],
    )
    df_with_dup = pd.concat([df, dup_row])

    cleaned, code, msg = clean_and_validate_bars(df_with_dup, current_dt, calendar, min_bars=60)
    assert code is None
    assert cleaned is not None
    assert len(cleaned) == 65
    assert cleaned["Close"].iloc[-1] == 102.0  # Keep last

    # Verify Close is NaN only gets dropped, but Volume NaN does NOT get dropped
    df_with_nans = df.copy()
    df_with_nans.loc[dates[10], "Close"] = np.nan
    df_with_nans.loc[dates[20], "Volume"] = np.nan

    cleaned_nans, code, msg = clean_and_validate_bars(df_with_nans, current_dt, calendar, min_bars=60)
    assert code is None
    assert cleaned_nans is not None
    assert len(cleaned_nans) == 64  # Close NaN row dropped
    assert np.isnan(cleaned_nans["Volume"].iloc[19])  # Volume NaN is preserved!


def test_stale_bar_detection():
    """Verify STALE_BAR is flagged when latest bar is more than MAX_BAR_AGE_SESSIONS behind."""
    calendar = MarketCalendar("NSE")
    current_dt = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    # Bars ending on Sep 15 (way more than 2 sessions behind Oct 1)
    dates = pd.date_range("2026-06-01", "2026-09-15", freq="B")
    df_stale = pd.DataFrame(
        {
            "Open": [100.0] * len(dates),
            "High": [105.0] * len(dates),
            "Low": [95.0] * len(dates),
            "Close": [100.0 + i for i in range(len(dates))],
            "Volume": [100_000.0] * len(dates),
        },
        index=dates,
    )

    cleaned, code, msg = clean_and_validate_bars(df_stale, current_dt, calendar, min_bars=60, max_bar_age_sessions=2)
    assert cleaned is None
    assert code == FailureCode.STALE_BAR


def test_insufficient_history():
    """Fewer than min_bars must return INSUFFICIENT_HISTORY."""
    calendar = MarketCalendar("NSE")
    current_dt = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    dates = pd.date_range("2026-08-01", periods=30, freq="B")
    df_short = pd.DataFrame(
        {"Close": [100.0] * len(dates), "Volume": [100_000.0] * len(dates)},
        index=dates,
    )

    cleaned, code, msg = clean_and_validate_bars(df_short, current_dt, calendar, min_bars=60)
    assert cleaned is None
    assert code == FailureCode.INSUFFICIENT_HISTORY

"""Data hygiene, indicator calculations (Wilder RSI, Volume Ratio), and bar validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Tuple
import numpy as np
import pandas as pd

from app.core.outcomes import FailureCode
from app.market.calendar import MarketCalendar


@dataclass
class IndicatorResult:
    """Calculated technical indicators and bar details for a symbol."""
    bar_date: date
    price: float
    rsi: float
    rsi_1d: float
    volume: int
    avg_volume_20d: float
    volume_ratio: float
    session_partial: bool


def clean_and_validate_bars(
    df: pd.DataFrame,
    current_dt: datetime,
    calendar: MarketCalendar,
    min_bars: int = 60,
    max_bar_age_sessions: int = 2,
) -> Tuple[Optional[pd.DataFrame], Optional[FailureCode], Optional[str]]:
    """Clean OHLCV DataFrame per Section 8 data hygiene rules.

    Rules:
    - Dedupe by date (keep last).
    - Normalise index to exchange-local dates.
    - Drop rows where Close is NaN only (do NOT drop volume NaNs).
    - Check for empty / all-NaN -> DELISTED.
    - Check min_bars -> INSUFFICIENT_HISTORY.
    - Check stale bar -> STALE_BAR.

    Returns:
        (cleaned_df, failure_code, failure_message)
    """
    if df is None or df.empty:
        return None, FailureCode.DELISTED, "No bar data returned, possibly delisted or symbol mismatch"

    # Verify Close column exists
    if "Close" not in df.columns:
        return None, FailureCode.DELISTED, "Missing Close column in bar data"

    # If all Close rows are NaN -> DELISTED
    if df["Close"].isna().all():
        return None, FailureCode.DELISTED, "All Close prices are NaN, possibly delisted"

    # Normalize DatetimeIndex to exchange-local dates and dedupe (keep last)
    # Convert index to timezone-aware if naive, then to exchange tz, then to date
    idx = pd.to_datetime(df.index)
    if idx.tz is None:
        idx = idx.tz_localize(calendar.tz)
    idx_local = idx.tz_convert(calendar.tz)
    df = df.copy()
    df.index = idx_local.normalize()

    # Dedupe by date (keep last)
    df = df[~df.index.duplicated(keep="last")]

    # Drop rows where Close is NaN ONLY (leave Volume NaNs intact for explicit detection)
    df = df[df["Close"].notna()]

    # Sort index chronologically
    df = df.sort_index()

    # Check minimum history
    if len(df) < min_bars:
        return None, FailureCode.INSUFFICIENT_HISTORY, (
            f"Insufficient history: {len(df)} bars found, minimum {min_bars} required"
        )

    # Check for stale bar
    latest_bar_date = df.index[-1].date()
    if calendar.is_stale_bar(latest_bar_date, current_dt, max_bar_age_sessions):
        sessions_behind = calendar.sessions_behind(latest_bar_date, current_dt)
        return None, FailureCode.STALE_BAR, (
            f"Latest bar date {latest_bar_date} is {sessions_behind} sessions behind "
            f"(threshold: {max_bar_age_sessions})"
        )

    return df, None, None


def compute_wilder_rsi(
    close_series: pd.Series,
    period: int = 14,
) -> Tuple[Optional[float], Optional[float], Optional[FailureCode], Optional[str]]:
    """Compute Wilder's RSI using ewm(alpha=1/period, adjust=False).

    Check order per Section 9:
    1. Flat series first: (avg_gain == 0 and avg_loss == 0) -> FLAT_SERIES.
    2. Zero loss: (avg_loss == 0) -> RSI = 100.0.
    3. Normal formula: 100 - (100 / (1 + RS)).

    Returns:
        (rsi_value, rsi_1d, failure_code, failure_message)
    """
    if len(close_series) < period + 1:
        return None, None, FailureCode.INSUFFICIENT_HISTORY, (
            f"Not enough bars ({len(close_series)}) to calculate RSI({period})"
        )

    delta = close_series.diff()
    # First diff is NaN, replace or drop for calculation
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    # ewm with alpha=1/period, adjust=False (Wilder's smoothing)
    alpha = 1.0 / period
    avg_gain = gain.ewm(alpha=alpha, adjust=False).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False).mean()

    latest_gain = float(avg_gain.iloc[-1])
    latest_loss = float(avg_loss.iloc[-1])

    # Check 1: Flat series first (no price change or both zero)
    if (latest_gain == 0.0 and latest_loss == 0.0) or (close_series.nunique() <= 1):
        return None, None, FailureCode.FLAT_SERIES, "Price series is completely flat with zero movement"

    # Check 2: avg_loss == 0 -> RSI = 100.0
    if latest_loss == 0.0:
        return 100.0, 100.0, None, None

    # Calculate full series to get the trend
    rs_series = avg_gain / avg_loss
    rsi_series = 100.0 - (100.0 / (1.0 + rs_series))
    
    rsi = float(rsi_series.iloc[-1])
    rsi_1d = float(rsi_series.iloc[-2]) if len(rsi_series) > 1 else rsi
    return rsi, rsi_1d, None, None



def compute_volume_metrics(
    volume_series: pd.Series,
    lookback: int = 20,
    elapsed_fraction: float = 1.0,
    min_elapsed_fraction: float = 0.25,
) -> Tuple[Optional[int], Optional[float], Optional[float], Optional[FailureCode], Optional[str]]:
    """Compute latest volume, 20-day average volume, and volume ratio per Section 9.

    Rules:
    - current_volume = latest daily bar.
    - avg20 = mean of the previous `lookback` completed sessions. The latest bar is NOT included.
    - NaN on latest bar -> MISSING_VOLUME.
    - 0 or negative on latest bar -> INVALID_VOLUME.
    - Any NaN in lookback window -> MISSING_VOLUME.
    - avg20 <= 0 -> INVALID_VOLUME.
    - volume_ratio = current_volume / avg20.

    Returns:
        (current_volume, avg20, volume_ratio, failure_code, failure_message)
    """
    if len(volume_series) < lookback + 1:
        return None, None, None, FailureCode.INSUFFICIENT_HISTORY, (
            f"Volume series has {len(volume_series)} bars, requires at least {lookback + 1}"
        )

    latest_val = volume_series.iloc[-1]

    # Validate latest bar volume
    if pd.isna(latest_val):
        return None, None, None, FailureCode.MISSING_VOLUME, "Latest bar volume is NaN"

    try:
        current_volume = int(latest_val)
    except (ValueError, TypeError):
        return None, None, None, FailureCode.INVALID_VOLUME, f"Latest bar volume is non-numeric: {latest_val}"

    if current_volume <= 0:
        return None, None, None, FailureCode.INVALID_VOLUME, (
            f"Latest bar volume is {current_volume}, expected positive volume"
        )

    # Window of previous `lookback` completed sessions (excluding latest bar)
    window = volume_series.iloc[-1 - lookback : -1]

    # Any NaN in lookback window
    if window.isna().any():
        return None, None, None, FailureCode.MISSING_VOLUME, (
            f"Lookback window of {lookback} sessions contains NaN volume values"
        )

    avg20 = float(window.mean())
    if avg20 <= 0 or pd.isna(avg20):
        return None, None, None, FailureCode.INVALID_VOLUME, (
            f"Calculated {lookback}-day average volume is non-positive: {avg20}"
        )

    projected_volume = current_volume
    if elapsed_fraction > 0 and elapsed_fraction < 1.0:
        elapsed_fraction = max(elapsed_fraction, min_elapsed_fraction)
        projected_volume = current_volume / elapsed_fraction

    volume_ratio = projected_volume / avg20
    return current_volume, avg20, float(volume_ratio), None, None


def compute_indicators(
    df: pd.DataFrame,
    current_dt: datetime,
    calendar: MarketCalendar,
    rsi_period: int = 14,
    volume_lookback: int = 20,
    min_bars: int = 60,
    max_bar_age_sessions: int = 2,
    min_volume_projection_elapsed: float = 0.25,
) -> Tuple[Optional[IndicatorResult], Optional[FailureCode], Optional[str]]:
    """Clean data and calculate RSI, Volume Ratio, and session partial flag."""
    cleaned_df, fail_code, fail_msg = clean_and_validate_bars(
        df=df,
        current_dt=current_dt,
        calendar=calendar,
        min_bars=min_bars,
        max_bar_age_sessions=max_bar_age_sessions,
    )
    if fail_code is not None:
        return None, fail_code, fail_msg

    # RSI calculation
    rsi, rsi_1d, rsi_code, rsi_msg = compute_wilder_rsi(cleaned_df["Close"], period=rsi_period)
    if rsi_code is not None:
        return None, rsi_code, rsi_msg

    latest_bar_date = cleaned_df.index[-1].date()
    latest_close = float(cleaned_df["Close"].iloc[-1])
    session_partial = calendar.is_session_partial(latest_bar_date, current_dt)
    elapsed_fraction = calendar.get_session_elapsed_fraction(current_dt) if session_partial else 1.0
    
    # Volume calculation
    cur_vol, avg20, vol_ratio, vol_code, vol_msg = compute_volume_metrics(
        cleaned_df["Volume"],
        lookback=volume_lookback,
        elapsed_fraction=elapsed_fraction,
        min_elapsed_fraction=min_volume_projection_elapsed,
    )
    if vol_code is not None:
        return None, vol_code, vol_msg

    return (
        IndicatorResult(
            bar_date=latest_bar_date,
            price=latest_close,
            rsi=rsi,
            rsi_1d=rsi_1d,
            volume=cur_vol,
            avg_volume_20d=avg20,
            volume_ratio=vol_ratio,
            session_partial=session_partial,
        ),
        None,
        None,
    )

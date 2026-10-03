"""Synthetic market data fixtures for testing and offline CLI execution.

ALL DATA IN THIS MODULE IS SYNTHETIC AND FICTITIOUS.
NOT REAL MARKET DATA. LABELED SYNTHETIC.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from app.core.interfaces import UniverseSymbol

SYNTHETIC_BANNER = """
================================================================================
                           *** SYNTHETIC DATA ***
  This execution uses fictitious, synthetic fixtures for offline testing only.
  No real market data, constituents, or index values are being fetched or served.
================================================================================
"""

SYNTHETIC_UNIVERSE: List[UniverseSymbol] = [
    UniverseSymbol(symbol="TEST1", company_name="Synthetic Alpha Inc", ticker="TEST1.NS", market="NSE"),
    UniverseSymbol(symbol="TEST2", company_name="Synthetic Beta Ltd", ticker="TEST2.NS", market="NSE"),
    UniverseSymbol(symbol="TEST3", company_name="Synthetic Gamma Corp", ticker="TEST3.NS", market="NSE"),
    UniverseSymbol(symbol="TEST4", company_name="Synthetic Delta Energy", ticker="TEST4.NS", market="NSE"),
    UniverseSymbol(symbol="TEST5", company_name="Synthetic Epsilon Consumer", ticker="TEST5.NS", market="NSE"),
    UniverseSymbol(symbol="TEST6", company_name="Synthetic Zeta Materials", ticker="TEST6.NS", market="NSE"),
    UniverseSymbol(symbol="TEST7", company_name="Synthetic Eta Industrials", ticker="TEST7.NS", market="NSE"),
    UniverseSymbol(symbol="TEST8", company_name="Synthetic Theta Utilities", ticker="TEST8.NS", market="NSE"),
    UniverseSymbol(symbol="TEST9", company_name="Synthetic Iota Telecom", ticker="TEST9.NS", market="NSE"),
    UniverseSymbol(symbol="TEST10", company_name="Synthetic Kappa RealEstate", ticker="TEST10.NS", market="NSE"),
]

SYNTHETIC_PE: Dict[str, Optional[float]] = {
    "TEST1.NS": 14.5,   # Passes (< 20)
    "TEST2.NS": 18.0,   # Passes (< 20)
    "TEST3.NS": 10.0,   # Passes (< 20)
    "TEST4.NS": 15.0,   # Passes PE (filtered at Stage 1 RSI)
    "TEST5.NS": 12.0,   # Passes PE (filtered at Stage 1 Volume)
    "TEST6.NS": 28.5,   # Fails PE (> 20)
    "TEST7.NS": None,   # Missing PE
    "TEST8.NS": 16.0,   # Stale bar (fails earlier)
    "TEST9.NS": 14.0,   # Flat series (fails earlier)
    "TEST10.NS": 15.0,  # Missing volume (fails earlier)
}


def generate_synthetic_ohlcv(
    ticker: str,
    base_date: Optional[date] = None,
    num_bars: int = 100,
) -> pd.DataFrame:
    """Generate deterministic synthetic daily OHLCV bar data for fictitious tickers."""
    if base_date is None:
        base_date = date(2026, 10, 1)  # Recent trading session

    # Generate business day date index
    dates = pd.date_range(end=base_date, periods=num_bars, freq="B")

    # Base price and volume series
    np.random.seed(abs(hash(ticker)) % (2**31))

    if ticker == "TEST9.NS":
        # Flat series
        closes = np.full(num_bars, 100.0)
        volumes = np.full(num_bars, 100_000.0)
    elif ticker == "TEST8.NS":
        # Stale bar: end dates 15 days earlier
        dates = pd.date_range(end=base_date - timedelta(days=20), periods=num_bars, freq="B")
        closes = 100.0 + np.cumsum(np.random.normal(0.1, 1.0, num_bars))
        volumes = np.random.uniform(50_000, 150_000, num_bars)
    else:
        # Standard synthetic series
        drift = 0.3 if ticker in ("TEST1.NS", "TEST2.NS", "TEST3.NS") else -0.3
        closes = 100.0 + np.cumsum(np.random.normal(drift, 1.0, num_bars))
        # Ensure positive
        closes = np.maximum(closes, 10.0)

        # Baseline volume around 100k
        volumes = np.random.uniform(80_000, 120_000, num_bars)

        if ticker == "TEST1.NS":
            # Breakout: volume 3.5x average
            volumes[-1] = 350_000.0
            closes[-1] = closes[-2] * 1.03
        elif ticker == "TEST2.NS":
            # Breakout: volume 2.5x average
            volumes[-1] = 250_000.0
            closes[-1] = closes[-2] * 1.02
        elif ticker == "TEST3.NS":
            # Super breakout: volume 5x average, sharp upward gain
            volumes[-1] = 500_000.0
            closes[-5:] = closes[-6] * np.cumprod(np.full(5, 1.04))
        elif ticker == "TEST4.NS":
            # Fails RSI: downward trend
            closes[-15:] = closes[-16] * np.cumprod(np.full(15, 0.98))
            volumes[-1] = 300_000.0
        elif ticker == "TEST5.NS":
            # Fails volume: low volume ratio
            volumes[-1] = 110_000.0
        elif ticker == "TEST6.NS":
            # Passes stage 1, high PE
            volumes[-1] = 280_000.0
            closes[-1] = closes[-2] * 1.02
        elif ticker == "TEST7.NS":
            # Passes stage 1, but missing PE
            volumes[-1] = 280_000.0
            closes[-1] = closes[-2] * 1.02
        elif ticker == "TEST10.NS":
            # NaN volume on latest bar
            volumes[-1] = np.nan

    df = pd.DataFrame(
        {
            "Open": closes * 0.99,
            "High": closes * 1.01,
            "Low": closes * 0.98,
            "Close": closes,
            "Volume": volumes,
        },
        index=dates,
    )
    return df

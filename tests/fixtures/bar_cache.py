"""SYNTHETIC OHLCV for fictitious TEST1.NS; never used by a real provider."""
import numpy as np
import pandas as pd


def synthetic_bars(count=10, *, interval="1h", start="2026-09-01 03:45", close=100.0):
    index = pd.date_range(start, periods=count, freq=interval, tz="UTC")
    prices = np.full(count, close)
    return pd.DataFrame({"Open": prices-1, "High": prices+2, "Low": prices-2,
                         "Close": prices, "Volume": np.full(count, 1000)}, index=index)

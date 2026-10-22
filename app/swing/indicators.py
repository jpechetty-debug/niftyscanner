"""Causal S2 indicator recurrences; no pandas seed defaults are implicit."""
import math
import numpy as np


def ema(values, period: int) -> np.ndarray:
    data = np.asarray(values, dtype=float)
    if period < 1 or data.ndim != 1 or not len(data) or not np.isfinite(data).all():
        raise ValueError("EMA needs finite one-dimensional values and a positive period")
    result = np.empty(len(data))
    result[0] = data[0]
    alpha = 2 / (period + 1)
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def atr(high, low, close, period: int) -> np.ndarray:
    high, low, close = (np.asarray(v, dtype=float) for v in (high, low, close))
    if (period < 1 or len(close) < period or high.shape != close.shape or low.shape != close.shape
            or close.ndim != 1 or not all(np.isfinite(x).all() for x in (high, low, close))):
        raise ValueError("ATR needs equal finite arrays and sufficient history")
    previous = np.r_[close[0], close[:-1]]
    ranges = np.maximum.reduce([high - low, np.abs(high - previous), np.abs(low - previous)])
    result = np.full(len(close), np.nan)
    result[period - 1] = math.fsum(ranges[:period]) / period
    for i in range(period, len(close)):
        result[i] = (result[i - 1] * (period - 1) + ranges[i]) / period
    return result

"""Score calculation and deterministic ranking."""

from __future__ import annotations

from typing import List, Tuple
from app.core.config import Settings
from app.core.interfaces import ScanResultItem


def score_components(
    volume_ratio: float,
    rsi: float,
    pe: float,
    config: Settings,
) -> Tuple[float, float, float]:
    """Weighted (volume, RSI, P/E) contributions per Section 12; they sum to the composite score.

    Formula:
    V = (volume_ratio - MIN_VOLUME_RATIO) / (VOLUME_RATIO_CAP - MIN_VOLUME_RATIO)
    R = (RSI - MIN_RSI) / (RSI_CAP - MIN_RSI)
    P = dynamic piecewise function punishing <=0 or >=MAX_PE and peaking at P/E=10

    All components are clipped to [0.0, 1.0].
    Returns (W_V * V, W_R * R, W_P * P).
    """
    v_raw = (volume_ratio - config.MIN_VOLUME_RATIO) / (config.VOLUME_RATIO_CAP - config.MIN_VOLUME_RATIO)
    v_clip = min(max(v_raw, 0.0), 1.0)

    r_raw = (rsi - config.MIN_RSI) / (config.RSI_CAP - config.MIN_RSI)
    r_clip = min(max(r_raw, 0.0), 1.0)

    p_clip = 0.0
    if pe > 0 and pe < config.MAX_PE:
        optimal_pe = config.OPTIMAL_PE
        if pe <= optimal_pe:
            p_clip = pe / optimal_pe
        else:
            p_clip = (config.MAX_PE - pe) / (config.MAX_PE - optimal_pe)
    p_clip = min(max(p_clip, 0.0), 1.0)

    return (config.WEIGHT_VOLUME * v_clip, config.WEIGHT_RSI * r_clip, config.WEIGHT_PE * p_clip)


def calculate_composite_score(
    volume_ratio: float,
    rsi: float,
    pe: float,
    config: Settings,
) -> float:
    """Composite score per Section 12: the sum of the weighted components."""
    return sum(score_components(volume_ratio, rsi, pe, config))


def rank_results(results: List[ScanResultItem]) -> List[ScanResultItem]:
    """Sort results deterministically per Section 12.

    Rules:
    - Round score to 6 decimals before sorting, so ties are deterministic.
    - Sort order:
        1. score descending (-round(score, 6))
        2. volume_ratio descending (-volume_ratio)
        3. ticker ascending (ticker)
    - Uses unrounded display values, preserves original values in objects.
    """
    return sorted(
        results,
        key=lambda item: (
            -round(item.score, 6),
            -item.volume_ratio,
            item.ticker,
        ),
    )

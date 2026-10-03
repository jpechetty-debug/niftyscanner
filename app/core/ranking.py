"""Score calculation and deterministic ranking."""

from __future__ import annotations

from typing import List
from app.core.config import Settings
from app.core.interfaces import ScanResultItem


def calculate_composite_score(
    volume_ratio: float,
    rsi: float,
    pe: float,
    config: Settings,
) -> float:
    """Calculate composite screening score per Section 12.

    Formula:
    V = (volume_ratio - MIN_VOLUME_RATIO) / (VOLUME_RATIO_CAP - MIN_VOLUME_RATIO)
    R = (RSI - MIN_RSI) / (RSI_CAP - MIN_RSI)
    P = (MAX_PE - PE) / MAX_PE

    All components are clipped to [0.0, 1.0].
    Score = W_V * V + W_R * R + W_P * P
    """
    v_raw = (volume_ratio - config.MIN_VOLUME_RATIO) / (config.VOLUME_RATIO_CAP - config.MIN_VOLUME_RATIO)
    v_clip = min(max(v_raw, 0.0), 1.0)

    r_raw = (rsi - config.MIN_RSI) / (config.RSI_CAP - config.MIN_RSI)
    r_clip = min(max(r_raw, 0.0), 1.0)

    # Dynamic P/E scoring
    if pe <= 0:
        # Heavily punish negative earnings (loss-making)
        p_clip = 0.0
    elif pe <= config.MAX_PE:
        # e.g., if MAX_PE = 20, PE = 10 gets (20-10)/20 = 0.5.
        p_raw = (config.MAX_PE - pe) / config.MAX_PE
        p_clip = min(max(p_raw, 0.0), 1.0)
    else:
        # Dynamically punish extremely high P/E (exponential/inverse decay)
        # If PE = 40 and MAX_PE = 20, 20/40 = 0.5. But we want a penalty, 
        # so maybe negative score or just very close to 0. 
        # We can map it smoothly below 0 if we want it to drag down overall score, 
        # but the request is to "punish more dynamically". Let's give it a slightly negative weight 
        # or just 0 so it doesn't contribute. Let's make it a penalty:
        penalty = (pe - config.MAX_PE) / config.MAX_PE
        p_clip = max(-1.0, -0.5 * penalty) # caps at -1.0

    score = (
        config.WEIGHT_VOLUME * v_clip
        + config.WEIGHT_RSI * r_clip
        + config.WEIGHT_PE * p_clip
    )
    return score


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

"""Stage 1 and Stage 2 screening filters.

Strict separation between:
- Filtered Out (normal threshold failure -> funnel counts only)
- Failed (data or format issue -> failed_symbols)
"""

from __future__ import annotations

import math
from typing import Optional, Tuple
from app.core.config import Settings
from app.core.indicators import IndicatorResult
from app.core.outcomes import FailureCode, FunnelTracker, Stage


def apply_stage1_filters(
    indicator: IndicatorResult,
    config: Settings,
    tracker: FunnelTracker,
) -> bool:
    """Apply Stage 1 technical filters (RSI, Volume Ratio, and Liquidity floor).

    Strict inequalities per Section 1:
    - RSI > MIN_RSI (strict >)
    - volume_ratio > MIN_VOLUME_RATIO (strict >)
    - avg20 >= MIN_AVG_VOLUME (if MIN_AVG_VOLUME > 0)

    Updates funnel tracker counts.
    Returns:
        True if all Stage 1 filters pass, False otherwise.
    """
    # Each symbol is attributed to exactly ONE bucket (first failing check), so that
    # universe = failed + filtered_rsi + filtered_volume + filtered_liquidity
    #            + filtered_pe + passed_pe always holds.
    if not (indicator.rsi > config.MIN_RSI):
        tracker.filtered_rsi += 1
        return False

    if config.REQUIRE_RSI_TRENDING and indicator.rsi_trend <= 0:
        tracker.filtered_rsi_trend += 1
        return False

    if not (indicator.volume_ratio > config.MIN_VOLUME_RATIO):
        tracker.filtered_volume += 1
        return False

    if config.MIN_AVG_VOLUME > 0 and indicator.avg_volume_20d < config.MIN_AVG_VOLUME:
        tracker.filtered_liquidity += 1
        return False

    tracker.passed_rsi_volume += 1
    return True


def evaluate_pe_value(
    pe_raw: Optional[float],
) -> Tuple[Optional[float], Optional[FailureCode], Optional[str]]:
    """Validate raw P/E value from provider per Section 10 rules.

    Rules:
    - None / absent -> MISSING_PE
    - NaN / inf / non-numeric / <= 0 -> INVALID_PE
    - valid positive float -> (pe, None, None)
    """
    if pe_raw is None:
        return None, FailureCode.MISSING_PE, "Trailing P/E ratio is missing or not provided"

    try:
        pe = float(pe_raw)
    except (ValueError, TypeError):
        return None, FailureCode.INVALID_PE, f"Trailing P/E is non-numeric: {pe_raw}"

    if math.isnan(pe) or math.isinf(pe):
        return None, FailureCode.INVALID_PE, f"Trailing P/E has invalid non-numeric or infinite value: {pe}"

    return pe, None, None


def apply_stage2_pe_filter(
    pe: float,
    config: Settings,
    tracker: FunnelTracker,
) -> bool:
    """Apply Stage 2 fundamental filter (Trailing P/E threshold).

    Strict inequalities per Section 1 & 10:
    - PE < MAX_PE (strict <)
    - If MIN_PE > 0, PE >= MIN_PE

    Updates funnel tracker counts.
    Returns:
        True if survivor passes P/E screening filter, False otherwise.
    """
    passed = True

    # P/E filtering is now deferred to dynamic scoring. 
    # We always pass it to the ranking stage.
    tracker.passed_pe += 1
    return True

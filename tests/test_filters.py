"""Unit tests for Stage 1 and Stage 2 filters, strict boundary checks, and funnel counts."""

from datetime import date
import pytest

from app.core.config import Settings
from app.core.filters import (
    apply_stage1_filters,
    apply_stage2_pe_filter,
    evaluate_pe_value,
)
from app.core.indicators import IndicatorResult
from app.core.outcomes import FailureCode, FunnelTracker


def make_dummy_indicator(rsi: float, vol_ratio: float, avg20: float = 100_000.0) -> IndicatorResult:
    """Helper to construct an IndicatorResult for filter testing."""
    return IndicatorResult(
        bar_date=date(2026, 10, 1),
        price=150.0,
        rsi=rsi,
        volume=int(avg20 * vol_ratio),
        avg_volume_20d=avg20,
        volume_ratio=vol_ratio,
        session_partial=False,
        rsi_trend=1.0,
    )


def test_stage1_exact_boundaries():
    """Verify strict inequalities for Stage 1 filters."""
    config = Settings(MIN_RSI=50.0, MIN_VOLUME_RATIO=2.0, MIN_AVG_VOLUME=0.0)

    # Case 1: Exact boundary RSI == 50.0 -> must FAIL (strict >)
    tracker = FunnelTracker()
    ind_exact_rsi = make_dummy_indicator(rsi=50.0, vol_ratio=3.0)
    assert apply_stage1_filters(ind_exact_rsi, config, tracker) is False
    assert tracker.filtered_rsi == 1
    assert tracker.passed_rsi_volume == 0

    # Case 2: Exact boundary Volume Ratio == 2.0 -> must FAIL (strict >)
    tracker = FunnelTracker()
    ind_exact_vol = make_dummy_indicator(rsi=55.0, vol_ratio=2.0)
    assert apply_stage1_filters(ind_exact_vol, config, tracker) is False
    assert tracker.filtered_volume == 1
    assert tracker.passed_rsi_volume == 0

    # Case 3: Just above both thresholds -> must PASS
    tracker = FunnelTracker()
    ind_pass = make_dummy_indicator(rsi=50.001, vol_ratio=2.001)
    assert apply_stage1_filters(ind_pass, config, tracker) is True
    assert tracker.passed_rsi_volume == 1
    assert tracker.filtered_rsi == 0
    assert tracker.filtered_volume == 0


def test_stage2_pe_boundaries():
    """Verify strict inequalities for Stage 2 P/E filter."""
    config = Settings(MAX_PE=20.0, MIN_PE=0.0)

    tracker = FunnelTracker()
    assert apply_stage2_pe_filter(20.0, config, tracker) is False  # strict <
    assert apply_stage2_pe_filter(-5.0, config, tracker) is False  # strict >= MIN_PE
    assert apply_stage2_pe_filter(500.0, config, tracker) is False # strict < MAX_PE
    assert tracker.passed_pe == 0
    assert tracker.filtered_pe == 3

    assert apply_stage2_pe_filter(15.0, config, tracker) is True
    assert tracker.passed_pe == 1


def test_evaluate_pe_value_classifications():
    """Test raw P/E classification into MISSING_PE, INVALID_PE, and valid float."""
    # Absent / None
    val, code, _ = evaluate_pe_value(None)
    assert val is None
    assert code == FailureCode.MISSING_PE

    # Non-numeric
    val, code, _ = evaluate_pe_value("N/A")
    assert val is None
    assert code == FailureCode.INVALID_PE

    # Non-positive (<= 0) are now valid
    val, code, _ = evaluate_pe_value(0.0)
    assert val == 0.0
    assert code is None

    val, code, _ = evaluate_pe_value(-5.0)
    assert val == -5.0
    assert code is None

    # NaN / Inf
    val, code, _ = evaluate_pe_value(float("nan"))
    assert val is None
    assert code == FailureCode.INVALID_PE

    val, code, _ = evaluate_pe_value(float("inf"))
    assert val is None
    assert code == FailureCode.INVALID_PE

    # Valid
    val, code, _ = evaluate_pe_value(15.5)
    assert val == 15.5
    assert code is None

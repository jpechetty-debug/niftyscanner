"""Unit tests for composite scoring, clipping, and deterministic ranking."""

import pytest

from app.core.config import Settings
from app.core.interfaces import ScanResultItem
from app.core.ranking import calculate_composite_score, rank_results


def test_composite_score_and_clipping():
    """Verify component normalization, clipping to [0, 1], and weighted sum."""
    config = Settings(
        MIN_VOLUME_RATIO=2.0,
        VOLUME_RATIO_CAP=10.0,
        MIN_RSI=50.0,
        RSI_CAP=80.0,
        MAX_PE=20.0,
        WEIGHT_VOLUME=0.40,
        WEIGHT_RSI=0.35,
        WEIGHT_PE=0.25,
    )

    # 1. Base values at minimums:
    # V = (2.0 - 2.0)/8 = 0.0
    # R = (50.0 - 50.0)/30 = 0.0
    # P = (20.0 - 20.0)/20 = 0.0 -> Score = 0.0
    score_min = calculate_composite_score(volume_ratio=2.0, rsi=50.0, pe=20.0, config=config)
    assert score_min == 0.0

    # 2. Values at or exceeding caps (clipping check):
    # vol_ratio=15.0 -> V clips to 1.0
    # rsi=95.0 -> R clips to 1.0
    # pe=0.0 -> P = (20-0)/20 = 1.0
    # Score = 0.40*1 + 0.35*1 + 0.25*1 = 1.0
    score_max = calculate_composite_score(volume_ratio=15.0, rsi=95.0, pe=0.0, config=config)
    assert score_max == 1.0

    # 3. Intermediate check:
    # vol_ratio = 6.0 -> V = (6 - 2)/8 = 0.5
    # rsi = 65.0 -> R = (65 - 50)/30 = 0.5
    # pe = 10.0 -> P = (20 - 10)/20 = 0.5
    # Score = 0.40*0.5 + 0.35*0.5 + 0.25*0.5 = 0.5
    score_mid = calculate_composite_score(volume_ratio=6.0, rsi=65.0, pe=10.0, config=config)
    assert pytest.approx(score_mid, abs=1e-6) == 0.5


def test_deterministic_ranking_and_tie_breaks():
    """Verify deterministic ranking with score, volume_ratio, and ticker tie-breaks."""
    item_a = ScanResultItem(
        ticker="AAA.NS",
        name="Company A",
        market="NSE",
        price=100.0,
        pe=10.0,
        rsi=60.0,
        volume=200000,
        avg_volume_20d=100000.0,
        volume_ratio=2.0,
        score=0.5000001,  # Rounds to 0.500000
        session_partial=False,
        bar_date="2026-10-01",
    )
    item_b = ScanResultItem(
        ticker="BBB.NS",
        name="Company B",
        market="NSE",
        price=120.0,
        pe=10.0,
        rsi=60.0,
        volume=300000,
        avg_volume_20d=100000.0,
        volume_ratio=3.0,  # Higher volume ratio
        score=0.5000004,  # Rounds to 0.500000 (tie with A)
        session_partial=False,
        bar_date="2026-10-01",
    )
    item_c = ScanResultItem(
        ticker="CCC.NS",
        name="Company C",
        market="NSE",
        price=150.0,
        pe=8.0,
        rsi=70.0,
        volume=400000,
        avg_volume_20d=100000.0,
        volume_ratio=4.0,
        score=0.75,  # Decisively higher score
        session_partial=False,
        bar_date="2026-10-01",
    )
    item_d = ScanResultItem(
        ticker="ZZZ.NS",
        name="Company Z",
        market="NSE",
        price=120.0,
        pe=10.0,
        rsi=60.0,
        volume=300000,
        avg_volume_20d=100000.0,
        volume_ratio=3.0,  # Same vol ratio as BBB
        score=0.5000003,  # Rounds to 0.500000 (tie with BBB on score and vol)
        session_partial=False,
        bar_date="2026-10-01",
    )

    ranked = rank_results([item_a, item_b, item_c, item_d])

    # Expect:
    # 1. item_c (score 0.75)
    # 2. item_b (tied score 0.500000, vol 3.0, ticker BBB comes before ZZZ)
    # 3. item_d (tied score 0.500000, vol 3.0, ticker ZZZ)
    # 4. item_a (tied score 0.500000, lower vol 2.0)
    assert ranked[0].ticker == "CCC.NS"
    assert ranked[1].ticker == "BBB.NS"
    assert ranked[2].ticker == "ZZZ.NS"
    assert ranked[3].ticker == "AAA.NS"

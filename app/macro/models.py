"""Pydantic data models for Macro Market Indicators (India VIX, Brent Crude, Nifty 50)."""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class MacroIndicatorItem(BaseModel):
    """Single macro market indicator reading."""
    symbol: str
    name: str
    current: float
    prev_close: Optional[float] = None
    change: float = 0.0
    change_pct: float = 0.0
    unit: str = ""
    sentiment: str = "Neutral"
    tone: str = "neutral"  # positive, negative, neutral, warning
    as_of: Optional[str] = None
    synthetic: bool = False


class MacroOverviewResponse(BaseModel):
    """Aggregated macro indicators payload returned by GET /api/macro."""
    indicators: List[MacroIndicatorItem] = Field(default_factory=list)
    market_regime: str = "Neutral"
    regime_tone: str = "neutral"  # positive, warning, negative, neutral
    last_refreshed: str
    synthetic: bool = False

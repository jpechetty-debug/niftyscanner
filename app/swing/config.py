"""Frozen S2 research defaults; inherited by the application's environment settings."""
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class SwingSettings(BaseSettings):
    SWING_PAPER_DB_PATH: str = "data/swing_paper.db"
    SWING_REPLAY_ENABLED: bool = False
    SWING_REPLAY_MAX_SESSIONS: int = Field(default=250, ge=1, le=5000)
    SWING_ENABLED: bool = False
    SWING_MODE: Literal["research"] = "research"
    SWING_CAPITAL_INR: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    SWING_EMA_FAST: int = Field(default=20, ge=2, le=500)
    SWING_EMA_MID: int = Field(default=50, ge=2, le=500)
    SWING_EMA_SLOW: int = Field(default=200, ge=2, le=1000)
    SWING_ATR_PERIOD: int = Field(default=14, ge=2, le=500)
    SWING_SLOPE_SESSIONS: int = Field(default=10, ge=1, le=500)
    SWING_RS_LOOKBACKS: str = "21,63"
    SWING_VOLUME_LOOKBACK: int = Field(default=20, ge=2, le=500)
    SWING_MIN_AVG_TRADED_VALUE_INR: float = Field(default=10000000, ge=0, allow_inf_nan=False)
    SWING_BREAKOUT_LOOKBACK: int = Field(default=20, ge=2, le=500)
    SWING_BREAKOUT_RVOL: float = Field(default=1.5, gt=0, allow_inf_nan=False)
    SWING_BREAKOUT_EXTENSION_ATR: float = Field(default=0.75, gt=0, allow_inf_nan=False)
    SWING_PULLBACK_LOOKBACK: int = Field(default=5, ge=1, le=100)
    SWING_PULLBACK_TOUCH_ATR: float = Field(default=0.25, ge=0, allow_inf_nan=False)
    SWING_PULLBACK_DEPTH_ATR: float = Field(default=3, gt=0, allow_inf_nan=False)
    SWING_PULLBACK_MAX_RVOL: float = Field(default=1.2, gt=0, allow_inf_nan=False)
    SWING_PULLBACK_RECLAIM_RVOL: float = Field(default=0.8, gt=0, allow_inf_nan=False)
    SWING_MAX_EXTENSION_ATR: float = Field(default=3, gt=0, allow_inf_nan=False)
    SWING_MAX_CANDLE_ATR: float = Field(default=2.5, gt=0, allow_inf_nan=False)
    SWING_MIN_CLOSE_LOCATION: float = Field(default=0.6, ge=0, le=1, allow_inf_nan=False)
    SWING_STOP_BUFFER_ATR: float = Field(default=0.2, ge=0, allow_inf_nan=False)
    SWING_MIN_STOP_ATR: float = Field(default=0.75, gt=0, allow_inf_nan=False)
    SWING_MAX_STOP_ATR: float = Field(default=3, gt=0, allow_inf_nan=False)
    SWING_TARGET_R: float = Field(default=2, gt=0, allow_inf_nan=False)
    SWING_OVERHEAD_LOOKBACK: int = Field(default=63, ge=2, le=500)
    SWING_OVERHEAD_BUFFER_ATR: float = Field(default=0.2, ge=0, allow_inf_nan=False)
    SWING_ENTRY_DIP_ATR: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    SWING_ENTRY_STOP_CLEARANCE_ATR: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    SWING_MAX_GAP_ATR: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    SWING_MIN_NET_RR: float = Field(default=1.5, gt=0, allow_inf_nan=False)
    SWING_ENTRY_VALID_SESSIONS: int = Field(default=1, ge=1, le=10)
    SWING_BREAKOUT_MAX_HOLD: int = Field(default=10, ge=1, le=100)
    SWING_PULLBACK_MAX_HOLD: int = Field(default=5, ge=1, le=100)
    SWING_RISK_FRACTION: float = Field(default=0.0025, gt=0, le=1, allow_inf_nan=False)
    SWING_MAX_POSITION_FRACTION: float = Field(default=0.10, gt=0, le=1, allow_inf_nan=False)
    SWING_MAX_GROSS_FRACTION: float = Field(default=0.60, gt=0, le=1, allow_inf_nan=False)
    SWING_MAX_OPEN_RISK_FRACTION: float = Field(default=0.015, gt=0, le=1, allow_inf_nan=False)
    SWING_MAX_SECTOR_FRACTION: float = Field(default=0.25, gt=0, le=1, allow_inf_nan=False)
    SWING_MAX_ADV_PARTICIPATION: float = Field(default=0.005, gt=0, le=1, allow_inf_nan=False)
    SWING_MAX_CORRELATION: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
    SWING_CORRELATION_SESSIONS: int = Field(default=63, ge=2, le=500)
    SWING_MIN_CORRELATION_OBSERVATIONS: int = Field(default=30, ge=2, le=500)

    @model_validator(mode="after")
    def validate_swing_rules(self):
        if not self.SWING_PAPER_DB_PATH.strip():
            raise ValueError("SWING_PAPER_DB_PATH cannot be empty")
        periods = [int(x) for x in self.SWING_RS_LOOKBACKS.split(",")]
        if not periods or periods != sorted(set(periods)) or not all(1 <= x <= 500 for x in periods):
            raise ValueError("SWING_RS_LOOKBACKS needs increasing unique session counts in [1,500]")
        if not self.SWING_EMA_FAST < self.SWING_EMA_MID < self.SWING_EMA_SLOW:
            raise ValueError("Swing EMA periods must increase")
        if self.SWING_MIN_STOP_ATR > self.SWING_MAX_STOP_ATR:
            raise ValueError("Swing stop bounds must increase")
        if self.SWING_MIN_CORRELATION_OBSERVATIONS > self.SWING_CORRELATION_SESSIONS:
            raise ValueError("Correlation observations exceed the window")
        # S1 import can intentionally request short diagnostic windows. Enforce warm-up
        # for evaluation in prepare_history, rather than breaking disabled S1 imports.
        return self

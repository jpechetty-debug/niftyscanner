"""Configuration module for stock screener.

Precedence: code defaults < environment / .env < data/settings.json.
Fail-fast validation is performed on startup.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, List

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment, .env, and optional settings.json."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Network & Server
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    UI_PORT: int = 8501
    API_BASE_URL: str = "http://127.0.0.1:8000"

    # Markets & Universes
    ENABLED_MARKETS: str = "NSE,NYSE"
    NSE_UNIVERSE_PATH: str = "data/nifty500.csv"
    NYSE_UNIVERSE_PATH: str = "data/otherlisted.txt"
    NYSE_EXCLUDE_SYMBOL_SUFFIXES: List[str] = [
        ".WS", ".WSA", ".WSB", ".U", ".UN", ".RT", ".R"
    ]
    NYSE_EXCLUDE_NAME_PATTERN: str = r"\b(preferred|warrants?|units?|rights?)\b"

    # Timing & Scheduling
    REFRESH_INTERVAL_SEC: int = 60
    MARKET_CLOSE_SCAN_DELAY_MIN: int = 20
    REFRESH_COOLDOWN_SEC: int = 30

    # Download & Data Fetching
    USE_JUGAAD_FOR_NSE: bool = False
    CHUNK_SIZE: int = 100
    CHUNK_DELAY_MIN_SEC: float = 1.0
    CHUNK_DELAY_MAX_SEC: float = 2.0
    DOWNLOAD_THREADS: int = 4
    PE_WORKERS: int = 4
    PE_CACHE_TTL_HOURS: int = 24

    # Resilience & Breakers
    MAX_RETRIES: int = 4
    BREAKER_THRESHOLD: int = 5
    BREAKER_COOLDOWN_SEC: int = 60
    SCAN_MIN_FETCH_RATIO: float = 0.5

    # Technical & Fundamental Filters
    RSI_PERIOD: int = 14
    MIN_RSI: float = 40.0
    RSI_CAP: float = 80.0
    REQUIRE_RSI_TREND_UP: bool = True
    MIN_VOLUME_RATIO: float = 1.5
    VOLUME_RATIO_CAP: float = 10.0
    VOLUME_LOOKBACK: int = 20
    MIN_AVG_VOLUME: float = 0.0
    MAX_PE: float = 50.0
    MIN_PE: float = -500.0
    MIN_BARS: int = 60
    MAX_BAR_AGE_SESSIONS: int = 2

    # Scoring Weights & Logging
    WEIGHT_VOLUME: float = 0.40
    WEIGHT_RSI: float = 0.35
    WEIGHT_PE: float = 0.25
    LOG_LEVEL: str = "INFO"

    @property
    def enabled_markets_list(self) -> List[str]:
        """Return ENABLED_MARKETS as a list of uppercase stripped market names."""
        return [m.strip().upper() for m in self.ENABLED_MARKETS.split(",") if m.strip()]

    @field_validator("REFRESH_INTERVAL_SEC")
    @classmethod
    def validate_refresh_interval(cls, v: int) -> int:
        if not (30 <= v <= 300):
            raise ValueError(f"REFRESH_INTERVAL_SEC must be between 30 and 300, got {v}")
        return v

    @model_validator(mode="after")
    def validate_business_rules(self) -> Settings:
        # Check weights sum to 1.0
        weight_sum = self.WEIGHT_VOLUME + self.WEIGHT_RSI + self.WEIGHT_PE
        if not math.isclose(weight_sum, 1.0, abs_tol=1e-6):
            raise ValueError(
                f"Weights (WEIGHT_VOLUME={self.WEIGHT_VOLUME}, WEIGHT_RSI={self.WEIGHT_RSI}, "
                f"WEIGHT_PE={self.WEIGHT_PE}) must sum to 1.0, got {weight_sum}"
            )

        # Check caps vs minimums
        if self.RSI_CAP <= self.MIN_RSI:
            raise ValueError(f"RSI_CAP ({self.RSI_CAP}) must be strictly greater than MIN_RSI ({self.MIN_RSI})")

        if self.VOLUME_RATIO_CAP <= self.MIN_VOLUME_RATIO:
            raise ValueError(
                f"VOLUME_RATIO_CAP ({self.VOLUME_RATIO_CAP}) must be strictly greater than "
                f"MIN_VOLUME_RATIO ({self.MIN_VOLUME_RATIO})"
            )

        if not (self.MAX_PE > self.MIN_PE):
            raise ValueError(
                f"P/E thresholds must satisfy MAX_PE > MIN_PE, "
                f"got MAX_PE={self.MAX_PE}, MIN_PE={self.MIN_PE}"
            )

        # Check history length relative to lookback
        if self.MIN_BARS < self.VOLUME_LOOKBACK + 1:
            raise ValueError(
                f"MIN_BARS ({self.MIN_BARS}) must be >= VOLUME_LOOKBACK + 1 ({self.VOLUME_LOOKBACK + 1})"
            )

        return self


def load_settings(settings_file: str | Path = "data/settings.json") -> Settings:
    """Load settings with precedence: code defaults < .env / env vars < data/settings.json."""
    settings = Settings()
    path = Path(settings_file)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # data/settings.json only overrides REFRESH_INTERVAL_SEC per spec section 14
            if "REFRESH_INTERVAL_SEC" in data:
                new_interval = int(data["REFRESH_INTERVAL_SEC"])
                # Return updated settings instance with validation
                settings = settings.model_copy(update={"REFRESH_INTERVAL_SEC": new_interval})
                settings.validate_refresh_interval(settings.REFRESH_INTERVAL_SEC)
        except Exception as e:
            # If settings file is corrupted, fallback to base settings
            from loguru import logger
            logger.error(f"Failed to load settings from {path}: {e}")
    return settings

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
    NYSE_EXCLUDE_NAME_PATTERN: str = r"\b(?:preferred|warrants?|units?|rights?|notes?|debentures?|bonds?|acquisition|funds?)\b"
    NYSE_REQUIRE_NAME_PATTERN: str = r"\b(?:common\s+(?:stock|shares)|ordinary\s+shares|ADS|ADR|American\s+Depositary\s+(?:Shares|Receipts))\b"

    # Timing & Scheduling
    REFRESH_INTERVAL_SEC: int = 60
    MARKET_CLOSE_SCAN_DELAY_MIN: int = 20
    POST_CLOSE_RETRY_INTERVAL_SEC: int = Field(default=300, gt=0)
    REFRESH_COOLDOWN_SEC: int = 30
    HISTORY_MODE: str = "canonical"
    HISTORY_INTRADAY_INTERVAL_SEC: int = Field(default=0, ge=0)

    # Download & Data Fetching
    USE_JUGAAD_FOR_NSE: bool = False
    CHUNK_SIZE: int = Field(default=100, gt=0)
    CHUNK_DELAY_MIN_SEC: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    CHUNK_DELAY_MAX_SEC: float = Field(default=2.0, ge=0, allow_inf_nan=False)
    DOWNLOAD_THREADS: int = Field(default=4, gt=0)
    PE_WORKERS: int = Field(default=4, gt=0)
    PE_CACHE_TTL_HOURS: int = Field(default=24, gt=0)

    # Resilience & Breakers
    MAX_RETRIES: int = Field(default=4, gt=0)
    BREAKER_THRESHOLD: int = Field(default=5, gt=0)
    BREAKER_COOLDOWN_SEC: int = Field(default=60, gt=0)
    SCAN_MIN_FETCH_RATIO: float = Field(default=0.5, gt=0, le=1)

    # Technical & Fundamental Filters
    RSI_PERIOD: int = Field(default=14, gt=0)
    MIN_RSI: float = 40.0
    RSI_CAP: float = 80.0
    REQUIRE_RSI_TREND_UP: bool = True
    MIN_VOLUME_RATIO: float = 1.5
    VOLUME_RATIO_CAP: float = 10.0
    VOLUME_LOOKBACK: int = Field(default=20, gt=0)
    MIN_AVG_VOLUME: float = Field(default=100_000.0, ge=0, allow_inf_nan=False)
    MIN_VOLUME_PROJECTION_ELAPSED: float = 0.25
    OPTIMAL_PE: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    MAX_PE: float = 50.0
    MIN_PE: float = 1.0
    MIN_BARS: int = Field(default=60, gt=0)
    MAX_BAR_AGE_SESSIONS: int = 2

    # Scoring Weights & Logging
    WEIGHT_VOLUME: float = Field(default=0.40, ge=0, allow_inf_nan=False)
    WEIGHT_RSI: float = Field(default=0.35, ge=0, allow_inf_nan=False)
    WEIGHT_PE: float = Field(default=0.25, ge=0, allow_inf_nan=False)
    LOG_LEVEL: str = "INFO"
    SQLITE_BUSY_TIMEOUT_SEC: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)

    # Forward signal evaluation (NSE price index; independent of live screening).
    PERFORMANCE_ENABLED: bool = True
    PERFORMANCE_DATA_DIR: str = "data"
    PERFORMANCE_NIGHTLY_TIME: str = "21:00"
    PERFORMANCE_BENCHMARK: str = "^CRSLDX"
    PERFORMANCE_PRICE_BASIS: str = "yahoo_split_adjusted_price"
    PERFORMANCE_BATCH_SIZE: int = Field(default=5, gt=0, le=100)
    PERFORMANCE_SYNC_CHUNK: int = Field(default=5000, gt=0, le=50000)
    PERFORMANCE_RETRY_SEC: int = Field(default=300, gt=0)
    PERFORMANCE_MAX_RETRIES: int = Field(default=3, gt=0, le=10)
    PERFORMANCE_TIMEOUT_SEC: int = Field(default=10, gt=0, le=60)
    PERFORMANCE_MIN_SAMPLE: int = Field(default=20, gt=0)
    PERFORMANCE_SCORE_EDGES: str = "0,0.25,0.5,0.75,1"
    PERFORMANCE_VOLUME_EDGES: str = "0,1.5,3,5,10"
    PERFORMANCE_RSI_EDGES: str = "0,40,50,60,70,100"

    @model_validator(mode="after")
    def validate_performance(self) -> Settings:
        from datetime import time
        time.fromisoformat(self.PERFORMANCE_NIGHTLY_TIME)
        if len(self.PERFORMANCE_NIGHTLY_TIME) != 5:
            raise ValueError("PERFORMANCE_NIGHTLY_TIME must use HH:MM")
        if not self.PERFORMANCE_BENCHMARK.strip() or not self.PERFORMANCE_DATA_DIR.strip():
            raise ValueError("Performance benchmark and data directory cannot be empty")
        if self.PERFORMANCE_PRICE_BASIS != "yahoo_split_adjusted_price":
            raise ValueError("Unsupported performance price basis")
        for key in ("PERFORMANCE_SCORE_EDGES", "PERFORMANCE_VOLUME_EDGES", "PERFORMANCE_RSI_EDGES"):
            edges = [float(x) for x in getattr(self, key).split(",")]
            if len(edges) < 2 or not all(math.isfinite(x) for x in edges) or any(a >= b for a, b in zip(edges, edges[1:])):
                raise ValueError(f"{key} must contain increasing finite edges")
        return self

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
        import re
        if self.HISTORY_MODE not in {"canonical", "all"}:
            raise ValueError("HISTORY_MODE must be canonical or all")
        try:
            re.compile(self.NYSE_EXCLUDE_NAME_PATTERN)
            re.compile(self.NYSE_REQUIRE_NAME_PATTERN)
        except re.error as error:
            raise ValueError(f"Invalid NYSE instrument pattern: {error}") from error
        if not self.enabled_markets_list or set(self.enabled_markets_list) - {"NSE", "NYSE"}:
            raise ValueError("ENABLED_MARKETS must contain NSE and/or NYSE")
        if len(self.enabled_markets_list) != len(set(self.enabled_markets_list)):
            raise ValueError("ENABLED_MARKETS contains duplicates")
        if self.CHUNK_DELAY_MAX_SEC < self.CHUNK_DELAY_MIN_SEC:
            raise ValueError("CHUNK_DELAY_MAX_SEC must be >= CHUNK_DELAY_MIN_SEC")
        if not all(math.isfinite(v) for v in (self.MIN_RSI, self.RSI_CAP, self.MIN_VOLUME_RATIO, self.VOLUME_RATIO_CAP, self.MIN_PE, self.MAX_PE)):
            raise ValueError("Screening thresholds must be finite")
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

        if not (0.0 < self.MIN_VOLUME_PROJECTION_ELAPSED <= 1.0):
            raise ValueError(
                f"MIN_VOLUME_PROJECTION_ELAPSED must be in (0, 1], got {self.MIN_VOLUME_PROJECTION_ELAPSED}"
            )

        if not (self.MAX_PE > self.MIN_PE):
            raise ValueError(
                f"P/E thresholds must satisfy MAX_PE > MIN_PE, "
                f"got MAX_PE={self.MAX_PE}, MIN_PE={self.MIN_PE}"
            )

        if self.OPTIMAL_PE >= self.MAX_PE:
            raise ValueError("OPTIMAL_PE must be below MAX_PE")

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
                settings = Settings(**{**settings.model_dump(), "REFRESH_INTERVAL_SEC": new_interval})
        except Exception as e:
            # If settings file is corrupted, fallback to base settings
            from loguru import logger
            logger.error(f"Failed to load settings from {path}: {e}")
    return settings

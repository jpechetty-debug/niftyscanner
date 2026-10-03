"""Outcomes, failure classifications, and funnel accounting.

Distinguishes strictly between:
1. Filtered Out (normal business logic filter) - tracked in funnel only
2. Failed (data / network issue) - tracked in failed_symbols
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class Stage(str, Enum):
    UNIVERSE = "universe"
    DOWNLOAD = "download"
    INDICATORS = "indicators"
    PE = "pe"


class FailureCode(str, Enum):
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    FLAT_SERIES = "FLAT_SERIES"
    MISSING_VOLUME = "MISSING_VOLUME"
    INVALID_VOLUME = "INVALID_VOLUME"
    STALE_BAR = "STALE_BAR"
    MISSING_PE = "MISSING_PE"
    INVALID_PE = "INVALID_PE"
    PE_FETCH_FAILED = "PE_FETCH_FAILED"
    DOWNLOAD_ERROR = "DOWNLOAD_ERROR"
    EMPTY_CHUNK = "EMPTY_CHUNK"
    THROTTLED = "THROTTLED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    DELISTED = "DELISTED"
    UNKNOWN = "UNKNOWN"


class FailedSymbolItem(BaseModel):
    """Record of a symbol or chunk failure."""
    ticker: str
    stage: Stage
    code: FailureCode
    message: str


class FunnelCounts(BaseModel):
    """Funnel metrics tracking screening pipeline progression."""
    universe: int = 0
    fetched: int = 0
    failed: int = 0
    filtered_rsi: int = 0
    filtered_volume: int = 0
    passed_rsi_volume: int = 0
    filtered_pe: int = 0
    passed_pe: int = 0


@dataclass
class FunnelTracker:
    """Helper to accumulate funnel metrics and failed symbols safely."""
    universe: int = 0
    fetched: int = 0
    failed: int = 0
    filtered_rsi: int = 0
    filtered_volume: int = 0
    passed_rsi_volume: int = 0
    filtered_pe: int = 0
    passed_pe: int = 0
    failed_symbols: List[FailedSymbolItem] = field(default_factory=list)

    def record_failure(self, ticker: str, stage: Stage, code: FailureCode, message: str) -> None:
        """Record a failure item and increment the failure count."""
        self.failed_symbols.append(
            FailedSymbolItem(ticker=ticker, stage=stage, code=code, message=message)
        )
        self.failed += 1

    def to_counts(self) -> FunnelCounts:
        """Convert tracker numbers to Pydantic FunnelCounts."""
        return FunnelCounts(
            universe=self.universe,
            fetched=self.fetched,
            failed=self.failed,
            filtered_rsi=self.filtered_rsi,
            filtered_volume=self.filtered_volume,
            passed_rsi_volume=self.passed_rsi_volume,
            filtered_pe=self.filtered_pe,
            passed_pe=self.passed_pe,
        )

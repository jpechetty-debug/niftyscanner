"""Core protocols and data transfer models for stock screener."""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional, Protocol, Tuple, runtime_checkable
import pandas as pd
from pydantic import BaseModel, Field

from app.core.outcomes import FailedSymbolItem


class UniverseSymbol(BaseModel):
    """Constituent symbol in a market universe."""
    symbol: str
    company_name: str
    ticker: str  # Yahoo-ready ticker e.g. INFY.NS or AAPL
    market: str  # e.g. NSE, NYSE


class ScanResultItem(BaseModel):
    """Screening result item meeting all criteria and scored."""
    ticker: str
    name: str
    market: str
    price: float
    pe: float
    rsi: float
    volume: int
    avg_volume_20d: float
    volume_ratio: float
    score: float
    session_partial: bool
    rsi_trend: float
    bar_date: str  # ISO YYYY-MM-DD


@runtime_checkable
class Clock(Protocol):
    """Time abstraction protocol for deterministic testing."""
    def now(self) -> datetime:
        """Return the current time with timezone."""
        ...

    def sleep(self, seconds: float) -> None:
        """Pause execution for the specified duration."""
        ...


@runtime_checkable
class Universe(Protocol):
    """Protocol for loading constituents of a market."""
    def load(self) -> List[UniverseSymbol]:
        """Load and return constituent symbols."""
        ...


@runtime_checkable
class MarketDataProvider(Protocol):
    """Protocol for fetching market prices and fundamental metrics."""

    def download_bars(
        self, tickers: List[str]
    ) -> Tuple[Dict[str, pd.DataFrame], int, List[FailedSymbolItem]]:
        """Download daily historical bars for tickers.

        Returns:
            Tuple of:
            - Dict mapping ticker -> DataFrame of daily bars
            - Request count
            - List of chunk-level or symbol-level download failures
        """
        ...

    def fetch_pe_batch(
        self, tickers: List[str]
    ) -> Tuple[Dict[str, float], int, List[FailedSymbolItem]]:
        """Fetch trailing P/E for a list of tickers.

        Returns:
            Tuple of:
            - Dict mapping ticker -> valid P/E value
            - Request count
            - List of P/E failure items (MISSING_PE, INVALID_PE, PE_FETCH_FAILED)
        """
        ...

"""In-memory cache for historical OHLCV bars to reduce redundant yfinance downloads."""

from __future__ import annotations

from typing import Dict, Optional
import pandas as pd
from loguru import logger

from app.core.interfaces import Clock
from app.market.clock import SystemClock


class BarCache:
    """Stores historical OHLCV bars per ticker to minimize redundant downloads."""

    def __init__(self, clock: Optional[Clock] = None):
        self.clock = clock or SystemClock()
        self._cache: Dict[str, pd.DataFrame] = {}

    def get_bars(self, ticker: str) -> Optional[pd.DataFrame]:
        """Return the cached bars for a ticker, if any."""
        return self._cache.get(ticker)

    def set_bars(self, ticker: str, df: pd.DataFrame) -> None:
        """Cache the bars for a ticker."""
        self._cache[ticker] = df

    def update_bars(self, ticker: str, new_df: pd.DataFrame) -> pd.DataFrame:
        """Append new bars to the cached bars, remove duplicates, and return combined."""
        if ticker not in self._cache:
            self._cache[ticker] = new_df
            return new_df

        cached_df = self._cache[ticker]
        
        # Combine and drop duplicates (keep the newer row for overlapping dates)
        combined = pd.concat([cached_df, new_df])
        combined = combined[~combined.index.duplicated(keep='last')]
        combined.sort_index(inplace=True)
        
        # Keep up to 100 bars (more than enough for 60-bar requirement)
        combined = combined.tail(100)
        
        self._cache[ticker] = combined
        return combined
        
    def clear(self) -> None:
        self._cache.clear()

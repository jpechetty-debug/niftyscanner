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

    def is_consistent(self, ticker: str, new_df: pd.DataFrame, rel_tol: float = 0.002) -> bool:
        """False if overlapping *completed* bars disagree with the cache (price adjustment happened).

        auto_adjust=True rewrites ALL history on every split/dividend, so stitching a fresh 5-day
        delta onto old cached bars would create a fake price jump (and corrupt RSI).
        The newest bar is skipped because it can still change intraday.
        A delta with no shared date may hide skipped sessions, so it also forces a full refetch.
        """
        cached = self._cache.get(ticker)
        if cached is None or cached.empty or new_df is None or new_df.empty or "Close" not in new_df.columns:
            return True
        shared = cached.index.intersection(new_df.index)
        if len(shared) == 0:
            return False
        overlap = shared[:-1]
        if len(overlap) == 0:
            return True
        old = cached.loc[overlap, "Close"].astype(float)
        new = new_df.loc[overlap, "Close"].astype(float)
        mask = old.notna() & new.notna() & (old != 0)
        if not mask.any():
            return True
        return bool(((old[mask] - new[mask]).abs() / old[mask].abs() <= rel_tol).all())

    def invalidate(self, ticker: str) -> None:
        self._cache.pop(ticker, None)

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
        
        # Never shrink below the full download, so MIN_BARS > 100 keeps working after merges
        combined = combined.tail(max(len(cached_df), 100))
        
        self._cache[ticker] = combined
        return combined
        
    def clear(self) -> None:
        self._cache.clear()

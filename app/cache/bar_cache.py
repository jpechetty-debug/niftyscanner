"""In-memory cache for historical OHLCV bars to reduce redundant yfinance downloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional
import numpy as np
import pandas as pd

from app.core.interfaces import Clock
from app.market.clock import SystemClock


@dataclass(frozen=True)
class BarCacheKey:
    """Provider transformation identity, not an attestation of price-basis proof.

    Defaults name the pre-existing ticker-only daily yfinance namespace. Other
    providers/intervals/policies must be explicit; no alias guessing is applied.
    """
    ticker: str
    provider: str = "yfinance"
    interval: str = "1d"
    adjustment_policy: str = "auto_adjust"

    def __post_init__(self):
        for name in ("ticker", "provider", "interval", "adjustment_policy"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"Cache {name} must be a nonempty identity without surrounding whitespace")


class BarCache:
    """Isolate histories by provider, ticker, interval and adjustment policy."""

    def __init__(self, clock: Optional[Clock] = None):
        self.clock = clock or SystemClock()
        self._cache: Dict[BarCacheKey, pd.DataFrame] = {}

    @staticmethod
    def _key(ticker: str | BarCacheKey) -> BarCacheKey:
        return ticker if isinstance(ticker, BarCacheKey) else BarCacheKey(ticker)

    @staticmethod
    def _validate_limit(max_rows: int | None) -> None:
        if max_rows is not None and (isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows <= 0):
            raise ValueError("max_rows must be a positive integer or None")

    def get_bars(self, ticker: str | BarCacheKey) -> Optional[pd.DataFrame]:
        """Read one namespace; a string retains the legacy daily meaning."""
        return self._cache.get(self._key(ticker))

    def set_bars(self, ticker: str | BarCacheKey, df: pd.DataFrame, *, max_rows: int | None = None) -> None:
        """Replace one history; apply only an explicitly supplied retention bound."""
        self._validate_limit(max_rows)
        self._cache[self._key(ticker)] = df.tail(max_rows) if max_rows is not None else df

    def is_consistent(self, ticker: str | BarCacheKey, new_df: pd.DataFrame, rel_tol: float = 0.002,
                      *, completed_through: datetime | None = None) -> bool:
        """False if overlapping *completed* bars disagree with the cache (price adjustment happened).

        auto_adjust=True rewrites ALL history on every split/dividend, so stitching a fresh 5-day
        delta onto old cached bars would create a fake price jump (and corrupt RSI).
        The newest bar is skipped because it can still change intraday.
        A delta with no shared date may hide skipped sessions, so it also forces a full refetch.
        Those legacy rules apply to daily keys. Other intervals compare full
        OHLCV exactly, using only an explicit completed_through watermark.
        """
        key = self._key(ticker)
        cached = self._cache.get(key)
        if key.interval != "1d":
            return self._intraday_consistent(cached, new_df, completed_through)
        if completed_through is not None:
            raise ValueError("completed_through is supported only for explicit intraday keys")
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

    @staticmethod
    def _intraday_consistent(cached, new_df, completed_through):
        """Compare full OHLCV; callers compute the final-bar-start watermark.

        Without a watermark every shared bar is checked conservatively. This
        never guesses publication finality from the number of rows or the clock.
        """
        boundary = pd.Timestamp(completed_through) if completed_through is not None else None
        if boundary is not None and (pd.isna(boundary) or boundary.tzinfo is None):
            raise ValueError("completed_through must be a timezone-aware bar-start timestamp")
        if cached is None or cached.empty:
            return True
        fields = ["Open", "High", "Low", "Close", "Volume"]
        if new_df is None or new_df.empty or any(set(fields)-set(frame.columns) for frame in (cached, new_df)):
            return False
        for frame in (cached, new_df):
            if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None or frame.index.has_duplicates or frame.index.hasnans:
                return False
        shared = cached.index.intersection(new_df.index).sort_values()
        if shared.empty:
            return False
        overlap = shared if boundary is None else shared[shared <= boundary]
        if overlap.empty:
            return True  # Only explicitly excluded unfinished bars overlap.
        try:
            old = cached.loc[overlap, fields].to_numpy(dtype=float)
            new = new_df.loc[overlap, fields].to_numpy(dtype=float)
        except (TypeError, ValueError):
            return False
        return bool(np.isfinite(old).all() and np.isfinite(new).all() and (old == new).all())

    def invalidate(self, ticker: str | BarCacheKey) -> None:
        """Evict exactly one namespace; other intervals and providers survive."""
        self._cache.pop(self._key(ticker), None)

    def update_bars(self, ticker: str | BarCacheKey, new_df: pd.DataFrame,
                    *, max_rows: int | None = None) -> pd.DataFrame:
        """Append new bars to the cached bars, remove duplicates, and return combined."""
        self._validate_limit(max_rows)
        key = self._key(ticker)
        if key not in self._cache:
            self.set_bars(key, new_df, max_rows=max_rows)
            return self._cache[key]

        cached_df = self._cache[key]
        if (key.interval != "1d" and isinstance(cached_df.index, pd.DatetimeIndex)
                and isinstance(new_df.index, pd.DatetimeIndex) and cached_df.index.tz is not None
                and new_df.index.tz is not None and cached_df.index.tz != new_df.index.tz):
            # Equivalent aware instants must stay a DatetimeIndex after concat;
            # pandas otherwise creates an object index for different timezones.
            new_df = new_df.copy()
            new_df.index = new_df.index.tz_convert(cached_df.index.tz)
        
        # Combine and drop duplicates (keep the newer row for overlapping dates)
        combined = pd.concat([cached_df, new_df])
        combined = combined[~combined.index.duplicated(keep='last')]
        combined.sort_index(inplace=True)
        
        if max_rows is not None:
            combined = combined.tail(max_rows)
        elif key.interval == "1d":
            # Preserve the legacy daily floor, but never discard a larger full
            # incoming history just because the older cache was shorter.
            combined = combined.tail(max(len(cached_df), len(new_df), 100))
        # Intraday defaults retain the supplied history. Its future adapter owns
        # a period/warm-up-appropriate explicit row bound, rather than daily 100.
        
        self._cache[key] = combined
        return combined
        
    def clear(self) -> None:
        self._cache.clear()

"""In-memory trailing P/E cache with TTL and negative caching for MISSING_PE."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from loguru import logger

from app.core.interfaces import Clock
from app.core.outcomes import FailureCode
from app.market.clock import SystemClock


@dataclass
class PECacheEntry:
    """Cache entry holding P/E value or negative MISSING_PE signal."""
    value: Optional[float]
    is_missing: bool
    cached_at: datetime


class PECache:
    """In-memory P/E cache with TTL.

    Rules per Section 14:
    - TTL PE_CACHE_TTL_HOURS
    - Expired entries are never used
    - MISSING_PE is cached (as negative signal)
    - PE_FETCH_FAILED is NEVER cached
    """

    def __init__(self, ttl_hours: int = 24, clock: Optional[Clock] = None) -> None:
        self.ttl = timedelta(hours=ttl_hours)
        self.clock = clock or SystemClock()
        self._cache: Dict[str, PECacheEntry] = {}

    def get(self, ticker: str) -> Tuple[bool, Optional[float], Optional[FailureCode]]:
        """Look up P/E for a ticker.

        Returns:
            Tuple of (hit: bool, pe_value: Optional[float], failure_code: Optional[FailureCode])
            - If not in cache or expired: (False, None, None)
            - If valid cached P/E: (True, pe_value, None)
            - If cached as MISSING_PE: (True, None, FailureCode.MISSING_PE)
        """
        entry = self._cache.get(ticker)
        if entry is None:
            return False, None, None

        now = self.clock.now()
        if now - entry.cached_at > self.ttl:
            # Expired
            del self._cache[ticker]
            return False, None, None

        if entry.is_missing:
            return True, None, FailureCode.MISSING_PE

        return True, entry.value, None

    def set_valid_pe(self, ticker: str, pe: float) -> None:
        """Cache a valid positive trailing P/E value."""
        self._cache[ticker] = PECacheEntry(
            value=pe,
            is_missing=False,
            cached_at=self.clock.now(),
        )

    def set_missing_pe(self, ticker: str) -> None:
        """Cache a MISSING_PE outcome (valid negative signal)."""
        self._cache[ticker] = PECacheEntry(
            value=None,
            is_missing=True,
            cached_at=self.clock.now(),
        )

    def clear(self) -> None:
        """Clear all entries."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

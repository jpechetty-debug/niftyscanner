"""Scan-to-scan change tracking: new entries, dropped tickers and qualifying streaks (section 24)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.interfaces import ScanResultItem


class DroppedItem(BaseModel):
    """A ticker that qualified in the previous successful scan but not in the current one."""
    ticker: str
    name: str


class ScanChanges(BaseModel):
    """Difference between two consecutive successful scans of one market."""
    new_entries: List[str] = Field(default_factory=list)
    dropped: List[DroppedItem] = Field(default_factory=list)
    compared_to: Optional[str] = None


def annotate_changes(
    previous: List[ScanResultItem],
    current: List[ScanResultItem],
    previous_refreshed: Optional[datetime],
    now: datetime,
) -> ScanChanges:
    """Set is_new / first_seen_at / scans_qualified on `current` and return the scan diff.

    Without a baseline (no earlier successful scan, in memory or restored from disk),
    nothing is flagged as new or dropped, so a cold start never looks like a burst of signals.
    A streak is the run of consecutive successful scans in which the ticker qualified;
    failed scans neither extend nor break it.
    """
    stamp = now.isoformat()
    if previous_refreshed is None:
        for item in current:
            item.is_new, item.first_seen_at, item.scans_qualified = False, stamp, 1
        return ScanChanges()

    prior = {item.ticker: item for item in previous}
    new_entries: List[str] = []
    for item in current:
        before = prior.get(item.ticker)
        if before is None:
            item.is_new, item.first_seen_at, item.scans_qualified = True, stamp, 1
            new_entries.append(item.ticker)
        else:
            item.is_new = False
            item.first_seen_at = before.first_seen_at or previous_refreshed.isoformat()
            item.scans_qualified = before.scans_qualified + 1

    remaining = {item.ticker for item in current}
    dropped = [DroppedItem(ticker=item.ticker, name=item.name) for item in previous if item.ticker not in remaining]
    return ScanChanges(new_entries=new_entries, dropped=dropped, compared_to=previous_refreshed.isoformat())

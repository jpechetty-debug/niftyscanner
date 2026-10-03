"""Clock implementations for real-world execution and deterministic testing."""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Optional

from app.core.interfaces import Clock


class SystemClock(Clock):
    """System clock using real wall-clock time."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock(Clock):
    """Controllable clock for deterministic tests. Tests never sleep."""

    def __init__(self, initial_time: Optional[datetime] = None) -> None:
        if initial_time is None:
            self._current_time = datetime(2026, 10, 2, 10, 0, 0, tzinfo=timezone.utc)
        else:
            if initial_time.tzinfo is None:
                self._current_time = initial_time.replace(tzinfo=timezone.utc)
            else:
                self._current_time = initial_time

    def now(self) -> datetime:
        return self._current_time

    def sleep(self, seconds: float) -> None:
        """Advance time immediately without sleeping."""
        if seconds > 0:
            from datetime import timedelta
            self._current_time += timedelta(seconds=seconds)

    def set_time(self, new_time: datetime) -> None:
        """Explicitly set current simulated time."""
        if new_time.tzinfo is None:
            self._current_time = new_time.replace(tzinfo=timezone.utc)
        else:
            self._current_time = new_time

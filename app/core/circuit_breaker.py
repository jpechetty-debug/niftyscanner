"""Dual circuit breaker implementation for download and fundamental data requests."""

from __future__ import annotations

from enum import Enum
from typing import Optional
from loguru import logger

from app.core.interfaces import Clock
from app.market.clock import SystemClock


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Circuit breaker guarding against systemic API failures and rate limits."""

    def __init__(
        self,
        name: str,
        threshold: int = 5,
        cooldown_sec: float = 60.0,
        clock: Optional[Clock] = None,
    ) -> None:
        self.name = name
        self.threshold = threshold
        self.cooldown_sec = cooldown_sec
        self.clock = clock or SystemClock()

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._tripped_at: Optional[float] = None
        self._half_open_in_flight = False

    @property
    def state(self) -> CircuitState:
        """Current circuit state, automatically evaluating cooldown."""
        if self._state == CircuitState.OPEN:
            now_ts = self.clock.now().timestamp()
            if self._tripped_at is not None and (now_ts - self._tripped_at >= self.cooldown_sec):
                logger.info(
                    f"Circuit breaker '{self.name}' cooldown elapsed ({self.cooldown_sec}s). "
                    f"Transitioning to HALF_OPEN."
                )
                self._state = CircuitState.HALF_OPEN
                self._half_open_in_flight = False
        return self._state

    def is_available(self) -> bool:
        """Return True if calls are allowed through."""
        s = self.state
        if s == CircuitState.CLOSED:
            return True
        elif s == CircuitState.HALF_OPEN:
            # In HALF_OPEN, allow one trial call
            if not self._half_open_in_flight:
                self._half_open_in_flight = True
                return True
            return False
        return False

    def record_success(self) -> None:
        """Record a successful systemic call."""
        if self._state == CircuitState.HALF_OPEN:
            logger.info(f"Circuit breaker '{self.name}' trial call succeeded. Resetting to CLOSED.")
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._tripped_at = None
        self._half_open_in_flight = False

    def record_systemic_failure(self, reason: str = "") -> None:
        """Record a systemic network/throttling failure. Trips if threshold reached."""
        self._consecutive_failures += 1
        now_ts = self.clock.now().timestamp()

        if self._state == CircuitState.HALF_OPEN:
            # Immediate trip back to OPEN on trial failure
            logger.warning(
                f"Circuit breaker '{self.name}' trial call failed ({reason}). Re-tripping to OPEN."
            )
            self._state = CircuitState.OPEN
            self._tripped_at = now_ts
            self._half_open_in_flight = False
        elif self._consecutive_failures >= self.threshold:
            self._state = CircuitState.OPEN
            self._tripped_at = now_ts
            logger.error(
                f"Circuit breaker '{self.name}' tripped OPEN after {self._consecutive_failures} "
                f"consecutive systemic failures. Cooldown: {self.cooldown_sec}s. Reason: {reason}"
            )
        else:
            logger.warning(
                f"Circuit breaker '{self.name}' recorded systemic failure "
                f"({self._consecutive_failures}/{self.threshold}): {reason}"
            )

    def reset(self) -> None:
        """Explicitly reset breaker to CLOSED."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._tripped_at = None
        self._half_open_in_flight = False

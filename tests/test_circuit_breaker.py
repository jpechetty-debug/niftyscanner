"""Unit tests for CircuitBreaker states, transitions, cooldowns, and trial calls."""

from datetime import datetime, timezone
import pytest

from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.market.clock import FakeClock


def test_circuit_breaker_trips_to_open_at_threshold():
    """Breaker trips OPEN after threshold consecutive systemic failures."""
    clock = FakeClock()
    cb = CircuitBreaker(name="test", threshold=3, cooldown_sec=60.0, clock=clock)

    assert cb.state == CircuitState.CLOSED
    assert cb.is_available() is True

    # 1st failure
    cb.record_systemic_failure("500 internal server error")
    assert cb.state == CircuitState.CLOSED

    # 2nd failure
    cb.record_systemic_failure("429 rate limit exceeded")
    assert cb.state == CircuitState.CLOSED

    # 3rd failure -> Trips to OPEN
    cb.record_systemic_failure("connection timeout")
    assert cb.state == CircuitState.OPEN
    assert cb.is_available() is False


def test_circuit_breaker_success_resets_counter():
    """A success before reaching threshold resets the consecutive failure counter."""
    clock = FakeClock()
    cb = CircuitBreaker(name="test", threshold=3, cooldown_sec=60.0, clock=clock)

    cb.record_systemic_failure("timeout 1")
    cb.record_systemic_failure("timeout 2")
    assert cb.state == CircuitState.CLOSED

    # Intervening success
    cb.record_success()

    # Next failure should start from count 1 again
    cb.record_systemic_failure("timeout 3")
    assert cb.state == CircuitState.CLOSED


def test_circuit_breaker_cooldown_and_half_open_success():
    """After cooldown, transitions to HALF_OPEN. Trial success resets to CLOSED."""
    clock = FakeClock()
    cb = CircuitBreaker(name="test", threshold=2, cooldown_sec=60.0, clock=clock)

    cb.record_systemic_failure("err 1")
    cb.record_systemic_failure("err 2")
    assert cb.state == CircuitState.OPEN

    # Advance clock by 30s (less than cooldown) -> remains OPEN
    clock.sleep(30.0)
    assert cb.state == CircuitState.OPEN
    assert cb.is_available() is False

    # Advance clock by another 31s (total 61s > 60s cooldown) -> transitions to HALF_OPEN
    clock.sleep(31.0)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.is_available() is True  # 1 trial call allowed

    # Trial call succeeds -> Resets to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.is_available() is True


def test_circuit_breaker_half_open_failure_re_trips():
    """In HALF_OPEN, trial failure immediately trips back to OPEN."""
    clock = FakeClock()
    cb = CircuitBreaker(name="test", threshold=2, cooldown_sec=60.0, clock=clock)

    cb.record_systemic_failure("err 1")
    cb.record_systemic_failure("err 2")
    assert cb.state == CircuitState.OPEN

    # Elapse cooldown
    clock.sleep(61.0)
    assert cb.state == CircuitState.HALF_OPEN

    # Trial call fails
    cb.record_systemic_failure("trial failed")
    assert cb.state == CircuitState.OPEN
    assert cb.is_available() is False

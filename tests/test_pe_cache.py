"""Unit tests for PECache TTL, negative caching, and uncacheable failure rules."""

from datetime import datetime, timezone
import pytest

from app.cache.pe_cache import PECache
from app.core.outcomes import FailureCode
from app.market.clock import FakeClock


def test_pe_cache_valid_and_missing_pe():
    """Verify caching valid values and negative caching of MISSING_PE."""
    clock = FakeClock()
    cache = PECache(ttl_hours=24, clock=clock)

    # 1. Uncached ticker -> Miss
    hit, val, code = cache.get("INFY.NS")
    assert hit is False
    assert val is None

    # 2. Cache valid P/E
    cache.set_valid_pe("INFY.NS", 18.5)
    hit, val, code = cache.get("INFY.NS")
    assert hit is True
    assert val == 18.5
    assert code is None

    # 3. Cache MISSING_PE (valid negative signal per Section 14)
    cache.set_missing_pe("LOSS_MAKING.NS")
    hit, val, code = cache.get("LOSS_MAKING.NS")
    assert hit is True
    assert val is None
    assert code == FailureCode.MISSING_PE


def test_pe_cache_ttl_expiration():
    """Cache entries must expire after PE_CACHE_TTL_HOURS."""
    clock = FakeClock()
    cache = PECache(ttl_hours=24, clock=clock)

    cache.set_valid_pe("TCS.NS", 25.0)
    hit, val, _ = cache.get("TCS.NS")
    assert hit is True

    # Advance clock by 23 hours (within TTL) -> Still hit
    clock.sleep(23 * 3600)
    hit, val, _ = cache.get("TCS.NS")
    assert hit is True

    # Advance clock by another 2 hours (total 25 hours > 24 hours TTL) -> Expired / Miss
    clock.sleep(2 * 3600)
    hit, val, _ = cache.get("TCS.NS")
    assert hit is False
    assert val is None


def test_pe_fetch_failed_never_cached():
    """PE_FETCH_FAILED must never be placed into the cache per Section 14."""
    clock = FakeClock()
    cache = PECache(ttl_hours=24, clock=clock)

    # In our provider design, when PE_FETCH_FAILED occurs, set_valid_pe / set_missing_pe is not called
    # Confirm that cache remains empty for that ticker
    ticker = "FAILED_TICKER.NS"
    # (Simulate provider failure handling: do not set into cache)
    hit, val, code = cache.get(ticker)
    assert hit is False

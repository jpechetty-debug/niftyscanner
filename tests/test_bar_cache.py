"""SYNTHETIC interval/provider/adjustment isolation and legacy compatibility."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from app.cache.bar_cache import BarCache, BarCacheKey
from app.core.config import Settings
from app.market.clock import FakeClock
from app.providers.yfinance_provider import YFinanceProvider
from tests.fixtures.bar_cache import synthetic_bars


def test_every_identity_dimension_separates_reads_writes_merges_and_invalidation():
    cache = BarCache()
    base = BarCacheKey("TEST1.NS")
    keys = [base, replace(base, interval="1h"), replace(base, adjustment_policy="raw"),
            replace(base, provider="SYNTHETIC-provider"), replace(base, ticker="TEST2.NS")]
    originals = []
    for i, key in enumerate(keys):
        frame = synthetic_bars(10, close=100.+i*100)
        originals.append(frame)
        cache.set_bars(key, frame)
    assert_frame_equal(cache.get_bars("TEST1.NS"), originals[0])
    assert cache.get_bars(replace(base, interval="4h")) is None
    changed = originals[1].tail(2).copy()
    changed["Close"] = 999.
    cache.update_bars(keys[1], changed)
    assert cache.get_bars(keys[1])["Close"].iloc[-1] == 999.
    for i in (0, 2, 3, 4):
        assert_frame_equal(cache.get_bars(keys[i]), originals[i])
    cache.invalidate(keys[1])
    assert cache.get_bars(keys[1]) is None
    assert_frame_equal(cache.get_bars("TEST1.NS"), originals[0])
    cache.invalidate("TEST1.NS")
    assert cache.get_bars(base) is None and cache.get_bars(keys[2]) is not None
    cache.clear()
    assert all(cache.get_bars(key) is None for key in keys)


@pytest.mark.parametrize("field", ["ticker", "provider", "interval", "adjustment_policy"])
@pytest.mark.parametrize("bad", ["", "  ", " 1h", None])
def test_identity_is_immutable_and_rejects_ambiguous_empty_labels(field, bad):
    with pytest.raises(ValueError):
        replace(BarCacheKey("TEST1.NS"), **{field: bad})


def test_key_is_frozen_and_legacy_string_is_the_explicit_daily_key():
    key = BarCacheKey("TEST1.NS")
    with pytest.raises(FrozenInstanceError):
        key.interval = "1h"
    cache = BarCache()
    bars = synthetic_bars(interval="1d")
    cache.set_bars("TEST1.NS", bars)
    assert_frame_equal(cache.get_bars(key), bars)
    assert cache.is_consistent(key, bars.tail(2))
    cache.invalidate(key)
    assert cache.get_bars("TEST1.NS") is None


def test_hourly_history_retains_more_than_the_daily_limit_and_all_new_rows():
    cache, key = BarCache(), BarCacheKey("TEST1.NS", interval="1h")
    full = synthetic_bars(900)
    cache.set_bars(key, full)
    delta = synthetic_bars(120, start=str(full.index[-20].tz_localize(None)))
    merged = cache.update_bars(key, delta)
    assert len(merged) == 1000
    assert merged.index[0] == full.index[0] and merged.index[-1] == delta.index[-1]
    assert merged.index.is_monotonic_increasing and merged.index.is_unique
    assert cache.get_bars("TEST1.NS") is None


@pytest.mark.parametrize("interval", ["1d", "1h"])
def test_larger_full_history_is_not_truncated_to_a_shorter_cached_length(interval):
    cache, key = BarCache(), BarCacheKey("TEST1.NS", interval=interval)
    full = synthetic_bars(800, interval=interval)
    cache.update_bars(key, full.tail(20))
    actual = cache.update_bars(key, full)
    assert_frame_equal(actual, full, check_freq=False)


def test_explicit_hourly_bound_is_used_on_every_write_and_keeps_newest_rows():
    cache, key = BarCache(), BarCacheKey("TEST1.NS", interval="1h")
    full = synthetic_bars(900)
    cache.set_bars(key, full, max_rows=700)
    assert_frame_equal(cache.get_bars(key), full.tail(700))
    new = synthetic_bars(20, start=str((full.index[-1]+pd.Timedelta(hours=1)).tz_localize(None)))
    merged = cache.update_bars(key, new, max_rows=700)
    assert len(merged) == 700 and merged.index[-1] == new.index[-1]


@pytest.mark.parametrize("bound", [0, -1, True, 2.5])
def test_invalid_retention_bound_does_not_mutate_cached_history(bound):
    cache, key = BarCache(), BarCacheKey("TEST1.NS", interval="1h")
    original = synthetic_bars()
    cache.set_bars(key, original)
    for write in (cache.set_bars, cache.update_bars):
        with pytest.raises(ValueError, match="positive integer"):
            write(key, original.tail(2), max_rows=bound)
        assert_frame_equal(cache.get_bars(key), original)


@pytest.mark.parametrize("column", ["Open", "High", "Low", "Close", "Volume"])
def test_hourly_overlap_compares_all_fields_including_latest_bar(column):
    cache, key = BarCache(), BarCacheKey("TEST1.NS", interval="1h")
    full = synthetic_bars()
    cache.set_bars(key, full)
    delta = full.tail(3).copy()
    assert cache.is_consistent(key, delta)
    delta.iloc[-1, delta.columns.get_loc(column)] += 1
    assert cache.is_consistent(key, delta) is False
    # Caller has explicitly identified the last completed component start.
    assert cache.is_consistent(key, delta, completed_through=full.index[-2])
    delta.iloc[-2, delta.columns.get_loc(column)] += 1
    assert cache.is_consistent(key, delta, completed_through=full.index[-2]) is False


def test_hourly_completion_cutoff_is_explicit_aware_and_has_no_implicit_clock():
    clock = FakeClock()
    cache, key = BarCache(clock), BarCacheKey("TEST1.NS", interval="1h")
    bars = synthetic_bars()
    cache.set_bars(key, bars)
    changed = bars.tail(2).copy()
    changed["Volume"] += 1
    assert not cache.is_consistent(key, changed)
    clock.sleep(10_000)
    assert not cache.is_consistent(key, changed)
    assert cache.is_consistent(key, changed, completed_through=bars.index[-3])
    for invalid in (datetime(2026, 9, 1), pd.NaT):
        with pytest.raises(ValueError, match="timezone-aware"):
            cache.is_consistent(key, changed, completed_through=invalid)


@pytest.mark.parametrize("change", ["naive", "duplicate", "missing_column", "nan", "inf", "empty", "no_overlap"])
def test_hourly_incompatible_or_incomplete_overlap_requires_refetch(change):
    cache, key = BarCache(), BarCacheKey("TEST1.NS", interval="1h")
    full = synthetic_bars()
    cache.set_bars(key, full)
    delta = full.tail(3).copy()
    if change == "naive":
        delta.index = delta.index.tz_localize(None)
    elif change == "duplicate":
        delta = pd.concat([delta, delta.tail(1)])
    elif change == "missing_column":
        delta = delta.drop(columns=["High"])
    elif change in ("nan", "inf"):
        delta.iloc[0, delta.columns.get_loc("Open")] = float(change)
    elif change == "empty":
        delta = delta.iloc[:0]
    else:
        delta.index = delta.index+pd.Timedelta(days=10)
    assert cache.is_consistent(key, delta) is False


def test_equivalent_aware_timezones_align_by_instant_without_filling_gaps():
    cache, key = BarCache(), BarCacheKey("TEST1.NS", interval="1h")
    bars = synthetic_bars()
    cache.set_bars(key, bars)
    delta = bars.tail(3).copy()
    delta.index = delta.index.tz_convert("Asia/Kolkata")
    assert cache.is_consistent(key, delta)
    delta = delta.drop(delta.index[1])
    merged = cache.update_bars(key, delta)
    assert len(merged) == len(bars) and merged.index.is_unique
    assert isinstance(merged.index, pd.DatetimeIndex) and merged.index.tz == bars.index.tz
    assert cache.is_consistent(key, bars.tail(3))


def test_existing_daily_provider_cannot_mistake_hourly_raw_or_other_source_for_daily(monkeypatch):
    cache = BarCache()
    keys = [BarCacheKey("TEST1.NS", interval="1h"), BarCacheKey("TEST1.NS", adjustment_policy="raw"),
            BarCacheKey("TEST1.NS", provider="SYNTHETIC-archive")]
    untouched = synthetic_bars(500)
    for key in keys:
        cache.set_bars(key, untouched)
    provider = YFinanceProvider(Settings(_env_file=None), clock=FakeClock(), bar_cache=cache)
    calls = []
    daily = synthetic_bars(70, interval="1d")

    def download(**kwargs):
        calls.append(kwargs)
        assert kwargs["interval"] == "1d" and kwargs["auto_adjust"] is True
        return daily if kwargs["period"] == "6mo" else daily.tail(5)

    monkeypatch.setattr("app.providers.yfinance_provider.yf.download", download)
    first, count, failed = provider.download_bars(["TEST1.NS"])
    assert not failed and count == 1 and calls[-1]["period"] == "6mo"
    second, count, failed = provider.download_bars(["TEST1.NS"])
    assert not failed and count == 1 and calls[-1]["period"] == "5d"
    assert_frame_equal(first["TEST1.NS"], daily)
    assert_frame_equal(second["TEST1.NS"], daily, check_freq=False)
    for key in keys:
        assert_frame_equal(cache.get_bars(key), untouched)


def test_daily_adjustment_refetch_invalidates_only_its_own_policy(monkeypatch):
    cache = BarCache()
    hourly = BarCacheKey("TEST1.NS", interval="1h")
    raw = BarCacheKey("TEST1.NS", adjustment_policy="raw")
    daily = synthetic_bars(70, interval="1d")
    other = synthetic_bars(500)
    cache.set_bars("TEST1.NS", daily)
    for key in (hourly, raw):
        cache.set_bars(key, other)
    revised = daily.copy()
    revised["Close"] *= .5
    calls = []

    def download(**kwargs):
        calls.append(kwargs["period"])
        return revised.tail(5) if kwargs["period"] == "5d" else revised

    monkeypatch.setattr("app.providers.yfinance_provider.yf.download", download)
    provider = YFinanceProvider(Settings(_env_file=None), clock=FakeClock(), bar_cache=cache)
    result, count, failed = provider.download_bars(["TEST1.NS"])
    assert not failed and count == 2 and calls == ["5d", "6mo"]
    assert_frame_equal(result["TEST1.NS"], revised)
    for key in (hourly, raw):
        assert_frame_equal(cache.get_bars(key), other)

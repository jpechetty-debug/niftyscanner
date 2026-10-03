"""Unit tests for MarketCalendar and Clock abstractions."""

from datetime import date, datetime, timedelta, timezone
import zoneinfo
import pytest

from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock


def test_fake_clock_time_progression():
    """FakeClock must advance time deterministically without real sleep."""
    start_time = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    clock = FakeClock(initial_time=start_time)

    assert clock.now() == start_time

    # Sleep 300 seconds
    clock.sleep(300.0)
    assert clock.now() == start_time + timedelta(seconds=300)


def test_calendar_timezone_and_open_status():
    """Verify market calendar open/closed detection during trading and non-trading hours."""
    calendar = MarketCalendar("NSE")
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")

    # Oct 1, 2026 at 11:00 AM IST (Trading day, open hours)
    open_dt = datetime(2026, 10, 1, 11, 0, tzinfo=tz).astimezone(timezone.utc)
    assert calendar.is_market_open(open_dt) is True

    status_open = calendar.get_market_status(open_dt)
    assert status_open.status == "open"
    assert status_open.market == "NSE"

    # Oct 1, 2026 at 8:00 AM IST (Trading day, pre-market closed)
    pre_dt = datetime(2026, 10, 1, 8, 0, tzinfo=tz).astimezone(timezone.utc)
    assert calendar.is_market_open(pre_dt) is False

    # Oct 2, 2026 (Gandhi Jayanti, a weekday exchange holiday)
    holiday_dt = datetime(2026, 10, 2, 12, 0, tzinfo=tz).astimezone(timezone.utc)
    assert calendar.is_market_open(holiday_dt) is False
    status_holiday = calendar.get_market_status(holiday_dt)
    assert status_holiday.is_holiday is True


def test_session_partial_formula():
    """Verify: session_partial = (latest bar date == exchange-local today) AND (market currently open)."""
    calendar = MarketCalendar("NSE")
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")

    # 1. Market open on Oct 1, bar is from Oct 1 -> session_partial = True
    open_dt = datetime(2026, 10, 1, 11, 0, tzinfo=tz).astimezone(timezone.utc)
    bar_today = date(2026, 10, 1)
    assert calendar.is_session_partial(bar_today, open_dt) is True

    # 2. Market open on Oct 1, bar is from previous day (Sep 30) -> session_partial = False
    bar_yesterday = date(2026, 9, 30)
    assert calendar.is_session_partial(bar_yesterday, open_dt) is False

    # 3. Market closed on Oct 1 evening (18:00 IST), bar is from Oct 1 -> session_partial = False
    closed_dt = datetime(2026, 10, 1, 18, 0, tzinfo=tz).astimezone(timezone.utc)
    assert calendar.is_session_partial(bar_today, closed_dt) is False

"""Market calendar, trading sessions, and market status using exchange_calendars."""

from __future__ import annotations

from datetime import date, datetime, timezone
import zoneinfo
from typing import Optional
import exchange_calendars as xcals
import pandas as pd
from pydantic import BaseModel


class MarketStatusInfo(BaseModel):
    """Current market operational status."""
    market: str
    is_open: bool
    status: str  # "open" | "closed"
    exchange_time: str
    timezone: str
    last_session: str
    is_holiday: bool
    notice: str = "Market data provided by yfinance is delayed and not real-time."


class MarketCalendar:
    """Calendar adapter wrapping exchange_calendars."""

    # Map market identifiers to exchange_calendars identifiers
    MARKET_TO_CALENDAR = {
        "NSE": "XBOM",  # XBOM (BSE) and NSE share identical trading hours and holidays in India
        "XNSE": "XBOM",
        "XBOM": "XBOM",
        "NYSE": "XNYS",
        "XNYS": "XNYS",
    }

    TIMEZONES = {
        "NSE": "Asia/Kolkata",
        "XBOM": "Asia/Kolkata",
        "NYSE": "America/New_York",
        "XNYS": "America/New_York",
    }

    def __init__(self, market: str = "NSE") -> None:
        self.market = market.upper()
        cal_name = self.MARKET_TO_CALENDAR.get(self.market, "XBOM")
        self.calendar = xcals.get_calendar(cal_name)
        tz_name = self.TIMEZONES.get(self.market, "Asia/Kolkata")
        self.tz = zoneinfo.ZoneInfo(tz_name)

    def to_exchange_local(self, dt: datetime) -> datetime:
        """Convert any timezone-aware datetime to exchange-local datetime."""
        if dt.tzinfo is None:
            # Default naive datetimes to UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self.tz)

    def is_market_open(self, dt: datetime) -> bool:
        """Return True if market is currently open for trading at given UTC/aware datetime."""
        ts = pd.Timestamp(dt)
        return bool(self.calendar.is_open_at_time(ts))

    def get_last_expected_session(self, dt: datetime) -> date:
        """Return the date of the last expected trading session as of datetime dt.

        - If market is currently open: today's session.
        - If market is closed:
            - If today was a session and dt is after close: today's session.
            - If today was a session and dt is before open: previous session.
            - If today is holiday/weekend: most recent previous session.
        """
        local_dt = self.to_exchange_local(dt)
        local_date_str = local_dt.strftime("%Y-%m-%d")

        # If market is currently open, today is active session
        if self.is_market_open(dt):
            return local_dt.date()

        # If today is a session
        if self.calendar.is_session(local_date_str):
            today_sess = self.calendar.date_to_session(local_date_str)
            open_ts = self.calendar.session_open(today_sess)
            utc_ts = pd.Timestamp(dt)
            if utc_ts < open_ts:
                # Before market open today -> previous completed session
                prev_sess = self.calendar.previous_session(today_sess)
                return prev_sess.date()
            else:
                # After market close (or during after-hours) -> today is the last completed session
                return today_sess.date()
        else:
            # Weekend or holiday -> latest session on or before today
            sess = self.calendar.date_to_session(local_date_str, direction="previous")
            return sess.date()

    def last_session_close(self, dt: datetime) -> datetime:
        """UTC close time of the last expected session as of dt."""
        sess = pd.Timestamp(self.get_last_expected_session(dt))
        return self.calendar.session_close(sess).to_pydatetime()

    def sessions_behind(self, bar_date: date, current_dt: datetime) -> int:
        """Calculate how many trading sessions bar_date is behind the last expected session.

        0 = bar is from the last expected session.
        1 = bar is 1 session behind.
        > max_age_sessions = STALE_BAR.
        """
        expected_session = self.get_last_expected_session(current_dt)
        if bar_date >= expected_session:
            return 0

        bar_sess_str = bar_date.strftime("%Y-%m-%d")
        exp_sess_str = expected_session.strftime("%Y-%m-%d")

        # Map bar_date to valid session on or before it
        bar_sess = self.calendar.date_to_session(bar_sess_str, direction="previous")
        exp_sess = self.calendar.date_to_session(exp_sess_str, direction="previous")

        if bar_sess >= exp_sess:
            return 0

        # distance counts sessions inclusively (e.g. today and yesterday = 2)
        dist = self.calendar.sessions_distance(bar_sess, exp_sess)
        return max(0, dist - 1)

    def is_stale_bar(self, bar_date: date, current_dt: datetime, max_age_sessions: int) -> bool:
        """Return True if the bar is more than max_age_sessions behind."""
        behind = self.sessions_behind(bar_date, current_dt)
        return behind > max_age_sessions

    def is_session_partial(self, bar_date: date, current_dt: datetime) -> bool:
        """Return True if latest bar date is exchange-local today and market is currently open.

        Formula from Section 4:
        session_partial = (latest bar date == exchange-local today) AND (market currently open)
        """
        local_today = self.to_exchange_local(current_dt).date()
        return (bar_date == local_today) and self.is_market_open(current_dt)


    def get_market_status(self, dt: datetime) -> MarketStatusInfo:
        """Return comprehensive status info for API / UI."""
        local_dt = self.to_exchange_local(dt)
        local_date_str = local_dt.strftime("%Y-%m-%d")
        is_open = self.is_market_open(dt)
        is_session_today = self.calendar.is_session(local_date_str)
        last_expected = self.get_last_expected_session(dt)

        return MarketStatusInfo(
            market=self.market,
            is_open=is_open,
            status="open" if is_open else "closed",
            exchange_time=local_dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
            timezone=str(self.tz),
            last_session=last_expected.strftime("%Y-%m-%d"),
            is_holiday=not is_session_today,
        )

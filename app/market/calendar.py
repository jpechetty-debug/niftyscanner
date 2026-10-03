"""Market calendar, trading sessions, and market status using pandas_market_calendars."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import zoneinfo
from typing import Optional
import pandas_market_calendars as mcal
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
    """Calendar adapter wrapping pandas_market_calendars."""

    def __init__(self, market: str = "NSE") -> None:
        self.market = market.upper()
        # pandas_market_calendars explicitly supports NSE and NYSE
        try:
            self.calendar = mcal.get_calendar(self.market)
        except Exception:
            # Fallback if unknown
            if self.market in ("XBOM", "XNSE"):
                self.calendar = mcal.get_calendar("BSE")
            else:
                self.calendar = mcal.get_calendar("NYSE")
                
        self.tz = zoneinfo.ZoneInfo(self.calendar.tz.zone)
        self._schedule_cache = {}

    def to_exchange_local(self, dt: datetime) -> datetime:
        """Convert any timezone-aware datetime to exchange-local datetime."""
        if dt.tzinfo is None:
            # Default naive datetimes to UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self.tz)

    def _get_schedule(self, dt: datetime) -> pd.DataFrame:
        """Get trading schedule centered around dt (month +/- 2 months to ensure safety)."""
        local_dt = self.to_exchange_local(dt)
        year = local_dt.year
        month = local_dt.month
        # Cache by year/month
        key = (year, month)
        if key not in self._schedule_cache:
            start_dt = local_dt.replace(day=1) - timedelta(days=60)
            end_dt = local_dt.replace(day=28) + timedelta(days=35) 
            self._schedule_cache[key] = self.calendar.schedule(start_date=start_dt, end_date=end_dt)
        return self._schedule_cache[key]

    def _get_schedule_for_date_range(self, start_d: date, end_d: date) -> pd.DataFrame:
        """Fetch schedule precisely for a given range (used to calculate distance)."""
        return self.calendar.schedule(start_date=start_d, end_date=end_d)

    def is_market_open(self, dt: datetime) -> bool:
        """Return True if market is currently open for trading at given UTC/aware datetime."""
        sched = self._get_schedule(dt)
        ts = pd.Timestamp(dt).tz_convert("UTC") if dt.tzinfo else pd.Timestamp(dt, tz="UTC")
        if sched.empty:
            return False
        mask = (sched['market_open'] <= ts) & (sched['market_close'] >= ts)
        return bool(mask.any())

    def get_last_expected_session(self, dt: datetime) -> date:
        """Return the date of the last expected trading session as of datetime dt."""
        sched = self._get_schedule(dt)
        ts = pd.Timestamp(dt).tz_convert("UTC") if dt.tzinfo else pd.Timestamp(dt, tz="UTC")
        local_date = self.to_exchange_local(dt).date()
        
        # Filter schedule up to today
        past_sched = sched[sched.index.date <= local_date]
        if past_sched.empty:
            raise ValueError("No past sessions found in schedule")
            
        latest_sess = past_sched.iloc[-1]
        if latest_sess.name.date() == local_date:
            if ts < latest_sess['market_open']:
                # Before open, return previous session
                if len(past_sched) > 1:
                    return past_sched.iloc[-2].name.date()
                else:
                    raise ValueError("No previous sessions found")
            else:
                return local_date
        else:
            return latest_sess.name.date()

    def last_session_close(self, dt: datetime) -> datetime:
        """UTC close time of the last expected session as of dt."""
        last_date = self.get_last_expected_session(dt)
        sched = self._get_schedule(dt)
        try:
            row = sched.loc[pd.Timestamp(last_date)]
            return row['market_close'].to_pydatetime()
        except KeyError:
            # Fallback if not found perfectly
            return pd.Timestamp(last_date).tz_localize(self.tz).to_pydatetime()

    def sessions_behind(self, bar_date: date, current_dt: datetime) -> int:
        """Calculate how many trading sessions bar_date is behind the last expected session."""
        expected_session = self.get_last_expected_session(current_dt)
        if bar_date >= expected_session:
            return 0

        # distance counts sessions inclusively (e.g. today and yesterday = 2)
        # So we fetch schedule between bar_date and expected_session
        # length of this schedule - 1 = sessions behind.
        sched = self._get_schedule_for_date_range(bar_date, expected_session)
        if sched.empty:
            return 0
            
        dist = len(sched)
        return max(0, dist - 1)

    def is_stale_bar(self, bar_date: date, current_dt: datetime, max_age_sessions: int) -> bool:
        """Return True if the bar is more than max_age_sessions behind."""
        behind = self.sessions_behind(bar_date, current_dt)
        return behind > max_age_sessions

    def is_session_partial(self, bar_date: date, current_dt: datetime) -> bool:
        """Return True if latest bar date is exchange-local today and market is currently open."""
        local_today = self.to_exchange_local(current_dt).date()
        return (bar_date == local_today) and self.is_market_open(current_dt)

    def get_session_elapsed_fraction(self, dt: datetime) -> float:
        """Return the fraction of the current session that has elapsed [0.0 - 1.0]."""
        if not self.is_market_open(dt):
            return 1.0
            
        sched = self._get_schedule(dt)
        local_date = self.to_exchange_local(dt).date()
        ts = pd.Timestamp(local_date)
        
        if ts not in sched.index:
            return 1.0
            
        row = sched.loc[ts]
        open_ts = row['market_open']
        close_ts = row['market_close']
        
        utc_ts = pd.Timestamp(dt).tz_convert("UTC") if dt.tzinfo else pd.Timestamp(dt, tz="UTC")
        
        total_duration = (close_ts - open_ts).total_seconds()
        elapsed = (utc_ts - open_ts).total_seconds()
        
        if total_duration <= 0:
            return 1.0
            
        frac = elapsed / total_duration
        return max(0.01, min(1.0, frac))

    def get_market_status(self, dt: datetime) -> MarketStatusInfo:
        """Return comprehensive status info for API / UI."""
        local_dt = self.to_exchange_local(dt)
        local_date_str = local_dt.strftime("%Y-%m-%d")
        is_open = self.is_market_open(dt)
        
        sched = self._get_schedule(dt)
        ts = pd.Timestamp(local_dt.date())
        is_session_today = ts in sched.index
        
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

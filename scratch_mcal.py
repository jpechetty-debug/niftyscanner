import pandas_market_calendars as mcal
import pandas as pd
from datetime import datetime, date, timedelta
import zoneinfo

class MarketCalendarMcal:
    def __init__(self, market: str = "NSE"):
        self.market = market.upper()
        self.calendar = mcal.get_calendar(self.market)
        self.tz = zoneinfo.ZoneInfo(self.calendar.tz.zone)
        self._schedule_cache = {}
        
    def to_exchange_local(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
        return dt.astimezone(self.tz)
        
    def _get_schedule(self, dt: datetime) -> pd.DataFrame:
        local_dt = self.to_exchange_local(dt)
        year = local_dt.year
        month = local_dt.month
        # Cache by month to reduce lookup overhead
        key = (year, month)
        if key not in self._schedule_cache:
            start_dt = local_dt.replace(day=1) - timedelta(days=60)
            end_dt = local_dt.replace(day=28) + timedelta(days=35) # Overlaps next month safely
            self._schedule_cache[key] = self.calendar.schedule(start_date=start_dt, end_date=end_dt)
        return self._schedule_cache[key]

    def is_market_open(self, dt: datetime) -> bool:
        sched = self._get_schedule(dt)
        ts = pd.Timestamp(dt).tz_convert("UTC") if dt.tzinfo else pd.Timestamp(dt, tz="UTC")
        # Check if ts is between any open/close
        if sched.empty:
            return False
        mask = (sched['market_open'] <= ts) & (sched['market_close'] >= ts)
        return mask.any()

    def get_last_expected_session(self, dt: datetime) -> date:
        sched = self._get_schedule(dt)
        ts = pd.Timestamp(dt).tz_convert("UTC") if dt.tzinfo else pd.Timestamp(dt, tz="UTC")
        local_date = self.to_exchange_local(dt).date()
        
        # Filter schedule up to today
        past_sched = sched[sched.index.date <= local_date]
        if past_sched.empty:
            raise ValueError("No past sessions found")
            
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
        last_date = self.get_last_expected_session(dt)
        sched = self._get_schedule(dt)
        try:
            row = sched.loc[pd.Timestamp(last_date)]
            return row['market_close'].to_pydatetime()
        except KeyError:
            # Fallback if not found perfectly
            return pd.Timestamp(last_date).tz_localize(self.tz).to_pydatetime()

if __name__ == "__main__":
    c = MarketCalendarMcal("NSE")
    dt = datetime(2026, 10, 3, 12, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
    print("Is open:", c.is_market_open(dt))
    print("Last expected:", c.get_last_expected_session(dt))
    print("Last session close:", c.last_session_close(dt))

"""Select bounded history captures without delaying the current JSON snapshot."""

from datetime import datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.market.calendar import MarketCalendar
from app.performance.repository import strategy_id


def history_capture_key(
    payload: dict[str, Any], config: Settings, calendar: MarketCalendar,
) -> tuple[str, str, str] | None:
    """Return (session, strategy, slot), or None when history should be skipped.

    Capture time comes from the injected clock's persisted last_refreshed value.
    The canonical capture requires a completed bar after the post-close delay.
    Optional intraday slots are indicative and never replace canonical history.
    """
    import json

    meta, results = payload.get("meta", {}), payload.get("results", [])
    try:
        captured = datetime.fromisoformat(meta["last_refreshed"])
        if captured.tzinfo is None:
            return None
        session = str(meta.get("data_as_of", ""))[:10]
        if not session:
            session = max((r["bar_date"] for r in results), default="")
        cal = calendar.calendar
        if not session or not cal.is_session(session):
            return None
        close = cal.session_close(session).to_pydatetime()
        partial = any(r.get("session_partial", False) for r in results)
        if captured >= close + timedelta(minutes=config.MARKET_CLOSE_SCAN_DELAY_MIN) and not partial:
            slot = "post-close"
        elif config.HISTORY_INTRADAY_INTERVAL_SEC and calendar.is_market_open(captured):
            opened = cal.session_open(session).to_pydatetime()
            if not opened <= captured < close:
                return None
            elapsed = (captured - opened).total_seconds()
            slot = f"intraday-{int(elapsed // config.HISTORY_INTRADAY_INTERVAL_SEC)}"
        else:
            return None
    except (ValueError, TypeError, KeyError):
        return None
    strategy = strategy_id(json.dumps(payload.get("strategy_context"), sort_keys=True))
    return session, strategy, slot

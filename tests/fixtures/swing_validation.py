"""SYNTHETIC S4 research fixtures. Never imported by production code."""
from datetime import datetime, timezone
import math

from app.core.config import Settings
from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock
from app.swing.setups import configuration
from app.swing.validation_protocol import Fold, Gates, Study, Variant, Window


def study_fixture():
    cfg = Settings(_env_file=None, SWING_CAPITAL_INR=1000000)
    cal = MarketCalendar("NSE", cfg)
    days = [s.date() for s in cal.calendar.sessions_in_range("2026-01-05", "2026-12-31")]
    def window(name, start, end):
        return Window(id=name, start=days[start], end=days[end])
    folds = (Fold(train=window("train1", 0, 19), validation=window("validation1", 25, 44), test=window("test1", 50, 69)),
             Fold(train=window("train2", 0, 69), validation=window("validation2", 75, 94), test=window("test2", 100, 119)))
    holdout = window("terminal", 130, 169)
    ids = [w.id for f in folds for w in (f.train, f.validation, f.test)]+[holdout.id]
    variant = Variant(name="primary", role="primary", configuration=dict(configuration(cfg)),
                      accounts={name: "SYNTHETIC-"+name for name in ids})
    study = Study(id="SYNTHETIC-study", hypothesis="SYNTHETIC predeclared primary research hypothesis",
                  strategies=("DAILY_BREAKOUT_V1", "DAILY_PULLBACK_V1"), capital_inr=1000000,
                  search_budget=1, variants=(variant,), folds=folds, holdout=holdout,
                  gates=Gates(bootstrap_replicates=100))
    clock = FakeClock(datetime(2025, 12, 31, tzinfo=timezone.utc))
    return cfg, cal, clock, study


def metric_panel(n=120, *, clustered=True):
    """SYNTHETIC date clusters and flat-day NAV; fictitious TEST trades only."""
    sessions = [f"SYNTHETIC-session-{i:04}" for i in range(n)]
    daily, trades = [], []
    for i, day in enumerate(sessions):
        daily.append({"session": day, "nav": 1000000*(1+.0005*i), "benchmark_nav": 1000000,
                      "cash": 900000, "exposure": .1})
        # Alternating runs of winning/losing dates, many correlated signals per date.
        for ticker in range(5):
            pnl = (100 if (i//40) % 2 == 0 else -80) if clustered else 100
            trades.append({"plan_id": f"SYNTHETIC-TEST{ticker}-{i}", "signal_session": day,
                "pnl": pnl, "r": pnl/100, "sector": f"SYNTHETIC-sector-{ticker%3}",
                "buy_gross": 1000, "sell_gross": 1000+pnl, "participation": .001,
                "holding_sessions": 5})
    return {"sessions": sessions, "daily": daily, "trades": trades, "attempts": len(trades),
            "filled": len(trades), "capital": 1000000., "buy_multiplier": 1., "sell_multiplier": 1.}

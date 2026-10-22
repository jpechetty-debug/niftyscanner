"""SYNTHETIC daily TEST equities/index only. Never used by the API or CLI."""
from dataclasses import replace
from datetime import timedelta

import pandas as pd

from app.core.config import Settings
from app.market.calendar import MarketCalendar
from app.swing.models import DailyPrice
from app.swing.plans import Evidence


def setup_fixture(kind="breakout", end_session="2026-10-15"):
    cfg = Settings(_env_file=None, SWING_CAPITAL_INR=1000000)
    cal = MarketCalendar("NSE", config=cfg)
    end = pd.Timestamp(end_session)
    sessions = cal.calendar.sessions
    dates = [x.date() for x in sessions[sessions <= end][-400:]]
    stock, bench = [], []
    for i, day in enumerate(dates):
        close = 100 + i * .1
        stock.append(DailyPrice(ticker="TEST1.NS", session=day, open=close-.2, high=close+.8,
                                low=close-.8, close=close, volume=1000000, price_basis="raw", series="EQ", isin="TEST001"))
        bench.append(DailyPrice(ticker="^CRSLDX", session=day, instrument="index", close=100+i*.02,
                                price_basis="raw"))
    if kind == "pullback":
        for i, value in zip(range(394, 399), (139.1, 138.9, 138.8, 138.9, 139.0)):
            stock[i] = stock[i].model_copy(update={"open": value-.1, "high": value+.5,
                                                  "low": value-1.1, "close": value})
        stock[-1] = stock[-1].model_copy(update={"open": 139.1, "high": 140.05, "low": 138.9,
                                               "close": 139.85, "volume": 1000000})
    else:
        stock[-1] = stock[-1].model_copy(update={"open": 140, "high": 141.1, "low": 139.4,
                                               "close": 140.9, "volume": 2000000})
    now = cal.calendar.session_close(end).to_pydatetime() + timedelta(minutes=30)
    proof = Evidence("SYNTHETIC-VINTAGE", "SYNTHETIC-REVISION", now, ("SYNTHETIC-HASH",),
                     True, True, True, "SYNTHETIC-SECTOR", True, True, True)
    return cfg, cal, now, tuple(stock), tuple(bench), proof


def other_plan(plan, ticker="TEST2.NS", sector="SYNTHETIC-OTHER"):
    return replace(plan, id=ticker, ticker=ticker, evidence=replace(plan.evidence, sector=sector))

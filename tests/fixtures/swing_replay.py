"""SYNTHETIC, TEST-only execution observations; not imported by production modules."""
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
import math

import pandas as pd

from app.market.clock import FakeClock
from app.swing.execution import ExecutionObservation
from app.swing.ledger import PaperLedger
from app.swing.models import DailyPrice
from app.swing.replay import ReplayEngine
from app.swing.risk import ReturnPoint
from app.swing.setups import digest, evaluate
from tests.fixtures.swing_setups import setup_fixture


class SyntheticExecutionMarket:
    """SYNTHETIC fixture evidence, explicit per-side capacities and dated returns."""
    def __init__(self, calendar):
        self.calendar, self.observations, self.return_calls = calendar, {}, []

    def observe(self, ticker, session, vintage, as_of):
        return self.observations.get((ticker, session), ExecutionObservation(None, as_of, "SYNTHETIC-MISSING", "missing_bar", actions_verified=True))

    def returns(self, ticker, session, count, vintage, as_of):
        self.return_calls.append((ticker, session, vintage, as_of))
        dates = [s.date() for s in self.calendar.calendar.sessions if s.date() <= session][-count:]
        frequency = 1 if ticker == "TEST1.NS" else 2
        return tuple(ReturnPoint(day, .01*math.sin(i*frequency)) for i, day in enumerate(dates))

    def day(self, plan, session, *, opening=None, high=None, low=None, close=None, **proof):
        value = (plan.entry_min+plan.entry_max)/2 if opening is None else opening
        bar = DailyPrice(ticker=plan.ticker, session=session, open=value,
                         high=max(value, close if close is not None else value)+.1 if high is None else high,
                         low=min(value, close if close is not None else value)-.1 if low is None else low,
                         close=value if close is None else close, volume=1000000,
                         price_basis="raw", series="EQ", isin=plan.isin)
        available = self.calendar.calendar.session_close(str(session)).to_pydatetime()+timedelta(minutes=5)
        params = dict(actions_verified=True, tradable=True, buy_capacity=100000, sell_capacity=100000)
        params.update(proof)
        self.observations[plan.ticker, session] = ExecutionObservation(bar, available, "SYNTHETIC-EXECUTION", **params)
        index = DailyPrice(ticker="^CRSLDX", session=session, instrument="index", open=100, high=102, low=99,
                           close=101, price_basis="raw")
        self.observations["^CRSLDX", session] = ExecutionObservation(index, available, "SYNTHETIC-INDEX", actions_verified=True)


def replay_fixture(root: Path, **plan_changes):
    cfg, cal, now, stock, bench, proof = setup_fixture()
    cfg.SWING_ENABLED = cfg.SWING_REPLAY_ENABLED = True
    cfg.SWING_PAPER_DB_PATH = str(root/"SYNTHETIC-paper.db")
    clock = FakeClock(now)
    plan = evaluate("TEST1.NS", stock, bench, proof, cfg, cal, now).plans[0]
    if plan_changes:
        plan = replace(plan, **plan_changes)
        plan = replace(plan, id=digest(asdict(replace(plan, id=""))))
    ledger = PaperLedger(Path(cfg.SWING_PAPER_DB_PATH), clock)
    ledger.create_account("SYNTHETIC", "1000000.00", plan.session, "fund")
    market = SyntheticExecutionMarket(cal)
    engine = ReplayEngine(ledger, market, cal, cfg, clock)
    return cfg, cal, clock, plan, ledger, market, engine


def capture(fixture, *, key="capture", plans=None):
    cfg, cal, clock, plan, ledger, market, engine = fixture
    version = ledger.state("SYNTHETIC")["version"]
    def apply(conn, state, emit):
        ledger.capture(conn, "SYNTHETIC", plans or (plan,), emit)
        return engine.advance(conn, state, emit, "SYNTHETIC", plan.session)
    return ledger.command("SYNTHETIC", key, version, {"operation": "capture", "plans": [p.id for p in plans or (plan,)]}, apply)


def advance(fixture, session, key=None):
    cfg, cal, clock, plan, ledger, market, engine = fixture
    clock.set_time(cal.calendar.session_close(str(session)).to_pydatetime()+timedelta(minutes=10))
    version = ledger.state("SYNTHETIC")["version"]
    return ledger.command("SYNTHETIC", key or str(session), version, {"through": str(session)},
                          lambda conn, state, emit: engine.advance(conn, state, emit, "SYNTHETIC", session))

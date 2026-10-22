"""SYNTHETIC S6 campaigns and observed references; tests only, never live-served."""
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from app.market.clock import FakeClock
from app.swing.forward import ForwardService
from app.swing.forward_store import ForwardSettings
from app.swing.paper_service import PaperService
from app.swing.replay import ReplayEngine
from app.swing.service import SwingResearchService
from app.swing.setups import evaluate
from tests.fixtures.swing_replay import SyntheticExecutionMarket
from tests.fixtures.swing_setups import setup_fixture


def forward_fixture(root, monkeypatch):
    cfg, cal, now, stock, benchmark, proof = setup_fixture()
    cfg.SWING_ENABLED = cfg.SWING_REPLAY_ENABLED = True
    cfg.SWING_PAPER_DB_PATH = str(root/"SYNTHETIC-paper.db")
    cfg.SWING_DB_PATH = str(root/"SYNTHETIC-observations.db")
    cfg.SWING_ARTIFACT_DIR = str(root/"SYNTHETIC-artifacts")
    plan = evaluate("TEST1.NS", stock, benchmark, proof, cfg, cal, now).plans[0]
    seed = cal.calendar.previous_session(str(plan.session))
    clock = FakeClock(cal.calendar.session_close(seed).to_pydatetime()+timedelta(minutes=30))
    paper = PaperService(cfg, clock, cal)
    paper.ledger.create_account("SYNTHETIC", "1000000.00", seed.date(), "fund")
    paper.market = SyntheticExecutionMarket(cal)
    paper.repository = SimpleNamespace(
        verify=lambda v: {"snapshot_id": v, "artifact_verified": True},
        report=lambda v: {"membership_scope": "SYNTHETIC", "summary": {"complete_trailing_price_histories": 1},
                          "benchmark": {"status": "available"}, "limitations": ["SYNTHETIC source"]})
    monkeypatch.setattr(SwingResearchService, "snapshot", lambda self, vintage, at:
        ({"imported_at": at.isoformat()}, {"adapter": "SYNTHETIC", "members": [plan.ticker]}))
    settings = ForwardSettings(_env_file=None, SWING_FORWARD_DB_PATH=str(root/"SYNTHETIC-forward.db"))
    service = ForwardService(cfg, clock, cal, settings, paper)
    sessions = [s.date() for s in cal.calendar.sessions if s.date() >= plan.session][:30]
    registration = service.register("SYNTHETIC-campaign", "SYNTHETIC", sessions[0], sessions[-1],
                                    "SYNTHETIC predeclared breakout hypothesis", "register")
    engine = ReplayEngine(paper.ledger, paper.market, cal, cfg, clock)
    return service, clock, cal, plan, sessions, engine, registration


def paper_day(fixture, session, *, capture_plan=False, **market_options):
    service, clock, cal, plan, sessions, engine, registration = fixture
    clock.set_time(cal.calendar.session_close(str(session)).to_pydatetime()+timedelta(minutes=30))
    service.paper.market.day(plan, session, **market_options)
    ledger = service.paper.ledger
    version = ledger.state("SYNTHETIC")["version"]
    def apply(conn, state, emit):
        if capture_plan:
            ledger.capture(conn, "SYNTHETIC", (plan,), emit)
        return engine.advance(conn, state, emit, "SYNTHETIC-VINTAGE", session)
    return ledger.command("SYNTHETIC", "day-"+str(session), version, {"session": str(session)}, apply)

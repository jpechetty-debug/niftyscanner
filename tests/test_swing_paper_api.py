"""Isolated SYNTHETIC source-only API checks: no synthetic plans served over HTTP."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.market.clock import FakeClock
from app.swing.paper_service import PaperService
from scripts.swing_data import run_import
from tests.fixtures.swing_source import create_source


@pytest.fixture
def app_env(tmp_path):
    source, universe = create_source(tmp_path)
    cfg = Settings(_env_file=None, ENABLED_MARKETS="NSE", SWING_ENABLED=True, SWING_REPLAY_ENABLED=True,
                   NSE_UNIVERSE_PATH=str(universe), SWING_MIN_HISTORY_SESSIONS=3, SWING_DELIVERY_SESSIONS=3,
                   SWING_DB_PATH=str(tmp_path/"SYNTHETIC-observations.db"),
                   SWING_PAPER_DB_PATH=str(tmp_path/"SYNTHETIC-ledger.db"),
                   SWING_ARTIFACT_DIR=str(tmp_path/"SYNTHETIC-evidence"), PERFORMANCE_ENABLED=False)
    clock = FakeClock(datetime(2026,10,7,3,tzinfo=timezone.utc))
    report = run_import(cfg, clock, source)
    app = create_app(config=cfg, clock=clock, start_scheduler=False)
    funding = {"account_id": "SYNTHETIC", "capital_inr": "1000000.00", "session": "2026-10-05", "key": "fund"}
    replay = {"account_id": "SYNTHETIC", "vintage": report["snapshot_id"], "through": "2026-10-06", "expected_version": 1, "key": "advance"}
    return cfg, clock, app, funding, replay


def test_no_startup_account_and_explicit_funding_only(app_env):
    cfg, clock, app, funding, replay = app_env
    assert not Path(cfg.SWING_PAPER_DB_PATH).exists()
    with TestClient(app) as client:
        assert client.get("/api/swing/paper/accounts/SYNTHETIC").status_code == 503
        assert not Path(cfg.SWING_PAPER_DB_PATH).exists()
        funded = client.post("/api/swing/paper/accounts", json=funding)
        assert funded.status_code == 200 and funded.json()["version"] == 1
        assert client.post("/api/swing/paper/accounts", json=funding).json() == funded.json()
        assert client.post("/api/swing/paper/accounts", json={**funding,"capital_inr":"2"}).status_code == 409
        assert client.post("/api/swing/paper/accounts", json={**funding,"account_id":"OTHER","capital_inr":"0"}).status_code == 422
        assert client.post("/api/swing/paper/accounts", json={**funding,"account_id":"OTHER","bars":[]}).status_code == 422
        view = client.get("/api/swing/paper/accounts/SYNTHETIC").json()
        assert view["cash"] == "1000000.00" and not view["paper_eligible"]
        assert view["orders"] == {} and view["model_status"] == "RESEARCH_ONLY"


@pytest.mark.asyncio
async def test_queued_work_recovers_after_restart_and_runs_once_under_scan_lock(app_env):
    cfg, clock, app, funding, replay = app_env
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/swing/paper/accounts", json=funding)).status_code == 200
        queued = await client.post("/api/swing/replay", json=replay)
        assert queued.status_code == 202 and queued.json()["status"] == "QUEUED"
        assert (await client.post("/api/swing/replay", json=replay)).json() == queued.json()
        # Restart shares no cached balances or in-memory request queue.
        restarted = create_app(config=cfg, clock=clock, start_scheduler=False)
        scheduler = restarted.state.scheduler
        async with scheduler.state.scan_lock:
            await scheduler.evaluate_swing_if_idle()
            assert restarted.state.paper.ledger.job(queued.json()["id"])["status"] == "QUEUED"
        await scheduler.evaluate_swing_if_idle()
        done = restarted.state.paper.ledger.job(queued.json()["id"])
        assert done["status"] == "DONE", done
        assert done["result"]["captured_plan_ids"] == [] and done["result"]["source_funnel"]["failed_symbols"] == 2
        state = restarted.state.paper.ledger.state("SYNTHETIC")
        assert state["cash"] == 1000000 and state["positions"] == {}
        assert state["nav"][0]["benchmark_nav"] is None
        version = state["version"]
        await scheduler.evaluate_swing_if_idle()
        assert restarted.state.paper.ledger.state("SYNTHETIC")["version"] == version
        perf = restarted.state.paper.performance("SYNTHETIC")
        assert perf["resolved_trades"] == 0 and perf["resolved_net_pnl"] == "0.00"


def test_rollback_flag_preserves_reads_and_rejects_new_mutations(app_env):
    cfg, clock, app, funding, replay = app_env
    with TestClient(app) as client:
        client.post("/api/swing/paper/accounts", json=funding)
        queued = client.post("/api/swing/replay", json=replay).json()
        cfg.SWING_ENABLED = cfg.SWING_REPLAY_ENABLED = False
        assert client.post("/api/swing/replay", json=replay).status_code == 404
        assert client.get("/api/swing/paper/accounts/SYNTHETIC").status_code == 200
        assert client.get("/api/swing/paper/jobs/"+queued["id"]).status_code == 200
        events = client.get("/api/swing/paper/events", params={"account_id":"SYNTHETIC","limit":1}).json()
        assert events["next_sequence"] == 1 and events["events"][0]["kind"] == "ACCOUNT_FUNDED"
        assert client.get("/api/swing/paper/performance", params={"account_id":"SYNTHETIC"}).status_code == 200


@pytest.mark.asyncio
async def test_worker_does_not_block_api_and_rechecks_reservation_priority(app_env, monkeypatch):
    cfg, clock, app, funding, replay = app_env
    entered, release = threading.Event(), threading.Event()
    svc, scheduler = app.state.paper, app.state.scheduler
    svc.create_account("SYNTHETIC", "1000000.00", __import__('datetime').date(2026,10,5), "fund")
    job_id = svc.enqueue("SYNTHETIC", "advance", replay["vintage"], __import__('datetime').date(2026,10,6), 1)
    original = svc.process
    def blocking(job):
        assert scheduler.state.scan_lock.locked()
        entered.set()
        assert release.wait(5)
        return original(job)
    monkeypatch.setattr(svc, "process", blocking)
    scheduler._reserved_market = "NSE"
    await scheduler.evaluate_swing_if_idle()
    assert not entered.is_set()
    scheduler._reserved_market = None
    work = asyncio.create_task(scheduler.evaluate_swing_if_idle())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await asyncio.wait_for(client.get("/api/swing/status"), 1)).status_code == 200
    finally:
        release.set()
    await work
    assert svc.ledger.job(job_id)["status"] == "DONE"


@pytest.mark.asyncio
async def test_stale_job_conflict_is_terminal_and_has_no_financial_side_effect(app_env):
    cfg, clock, app, funding, replay = app_env
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/swing/paper/accounts", json=funding)
        assert (await client.post("/api/swing/replay", json={**replay,"expected_version":99})).status_code == 409
        job = (await client.post("/api/swing/replay", json=replay)).json()
        # Another valid command changes the version after durable acceptance.
        app.state.paper.ledger.command("SYNTHETIC", "concurrent", 1, {"operation":"SYNTHETIC"},
                                       lambda conn,state,emit: emit("REPLAY_RECORDED", {"SYNTHETIC":True}))
        await app.state.scheduler.evaluate_swing_if_idle()
        failed = app.state.paper.ledger.job(job["id"])
        assert failed["status"] == "FAILED" and failed["error"] == "PaperConflict"
        state = app.state.paper.ledger.state("SYNTHETIC")
        assert state["version"] == 2 and state["cash"] == 1000000
        assert app.state.paper.next_job() is None


def test_api_rejects_bar_injection_unknown_vintage_market_and_missing_promotion(app_env):
    cfg, clock, app, funding, replay = app_env
    with TestClient(app) as client:
        client.post("/api/swing/paper/accounts", json=funding)
        assert client.post("/api/swing/replay", json={**replay,"vintage":"a"*64}).status_code == 404
        assert client.post("/api/swing/replay", json={**replay,"fills":[]}).status_code == 422
        assert client.post("/api/swing/replay?market=NYSE", json=replay).status_code == 404
        assert client.post("/api/swing/replay?market=BAD", json=replay).status_code == 422
        assert client.post("/api/swing/paper/promote", json={}).status_code == 404
        assert client.get("/api/swing/paper/accounts/absent").status_code == 404
        assert client.get("/api/swing/plans/absent", params={"account_id":"SYNTHETIC"}).status_code == 404


@pytest.mark.asyncio
async def test_scan_reservation_arriving_during_job_preflight_keeps_priority(app_env,monkeypatch):
    cfg,clock,app,funding,replay=app_env
    from datetime import date
    svc,scheduler=app.state.paper,app.state.scheduler
    svc.create_account("SYNTHETIC","1000000.00",date(2026,10,5),"fund")
    job_id=svc.enqueue("SYNTHETIC","advance",replay["vintage"],date(2026,10,6),1)
    original=svc.next_job
    def preflight():
        job=original()
        scheduler._reserved_market="NSE"
        return job
    monkeypatch.setattr(svc,"next_job",preflight)
    await scheduler.evaluate_swing_if_idle()
    assert svc.ledger.job(job_id)["status"]=="QUEUED"
    scheduler._reserved_market=None

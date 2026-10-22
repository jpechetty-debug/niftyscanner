"""Isolated research API integration. No production store, network or served fixtures."""
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
from app.swing.api import ResearchResponse
from app.swing.repository import SwingRepository
from app.swing.service import SwingResearchService
from app.swing.setups import evaluate
from scripts.swing_data import run_import
from tests.fixtures.swing_setups import setup_fixture
from tests.fixtures.swing_source import create_source


@pytest.fixture
def isolated(tmp_path):
    # SYNTHETIC importer-only evidence. Results must remain empty/unavailable;
    # these isolated TestClient requests never run through the real web server.
    source, universe = create_source(tmp_path)
    cfg = Settings(_env_file=None, ENABLED_MARKETS="NSE", SWING_ENABLED=True,
                   NSE_UNIVERSE_PATH=str(universe), SWING_MIN_HISTORY_SESSIONS=3,
                   SWING_DELIVERY_SESSIONS=3, SWING_DB_PATH=str(tmp_path/"swing.db"),
                   SWING_ARTIFACT_DIR=str(tmp_path/"evidence"), PERFORMANCE_ENABLED=False)
    clock = FakeClock(datetime(2026, 10, 7, 3, tzinfo=timezone.utc))
    report = run_import(cfg, clock, source)
    app = create_app(config=cfg, clock=clock, start_scheduler=False)
    return cfg, clock, report, app


def test_read_only_unverified_data_reports_reasons_not_plans(isolated):
    cfg, clock, report, app = isolated
    original = Path(cfg.SWING_DB_PATH).read_bytes()
    with TestClient(app) as client:
        status = client.get("/api/swing/status").json()
        assert status["enabled"] and not status["scheduled"] and not status["broker_execution"]
        response = client.get("/api/swing/results", params={"vintage": report["snapshot_id"]})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["plans"] == [] and payload["model_status"] == "RESEARCH_ONLY"
        assert not payload["paper_eligible"] and payload["funnel"]["failed_symbols"] == 2
        assert all("benchmark_unavailable" in x["reasons"] for x in payload["failed"])
        quality = client.get("/api/swing/data-quality", params={"vintage": report["snapshot_id"]})
        assert quality.status_code == 200 and quality.json() == report
        assert client.post("/api/swing/results", json={"bars": []}).status_code == 405
    assert Path(cfg.SWING_DB_PATH).read_bytes() == original


def test_api_disabled_market_vintage_and_store_errors(isolated):
    cfg, clock, report, app = isolated
    with TestClient(app) as client:
        path = "/api/swing/results"
        params = {"vintage": report["snapshot_id"]}
        assert client.get(path, params={**params, "market": "INVALID"}).status_code == 422
        assert client.get(path, params={**params, "market": "NYSE"}).status_code == 404
        assert client.get(path, params={"vintage": "a"*64}).status_code == 404
        assert client.get(path, params={"vintage": "../x"}).status_code == 422
        cfg.SWING_ENABLED = False
        assert client.get(path, params=params).status_code == 404
        assert not client.get("/api/swing/status").json()["enabled"]
        cfg.SWING_ENABLED = True
        cfg.SWING_DB_PATH = str(Path(cfg.SWING_DB_PATH).with_name("absent.db"))
        assert client.get(path, params=params).status_code == 503
        assert not Path(cfg.SWING_DB_PATH).exists()


def test_changed_evidence_is_a_conflict_not_a_silent_read(isolated):
    cfg, clock, report, app = isolated
    artifact = Path(cfg.SWING_ARTIFACT_DIR)/"snapshots"/(report["snapshot_id"]+".jsonl")
    artifact.write_bytes(artifact.read_bytes()+b" ")
    with TestClient(app) as client:
        for endpoint in ("results", "data-quality"):
            assert client.get("/api/swing/"+endpoint, params={"vintage": report["snapshot_id"]}).status_code == 409


def test_future_import_vintage_cannot_be_evaluated(isolated):
    cfg, clock, report, app = isolated
    clock.set_time(datetime(2026, 10, 7, 2, tzinfo=timezone.utc))
    with TestClient(app) as client:
        assert client.get("/api/swing/results", params={"vintage": report["snapshot_id"]}).status_code == 404


def test_positive_plan_response_serialization_stays_research_only():
    # Validate the response schema directly: never publish SYNTHETIC plans via HTTP.
    cfg, cal, now, stock, bench, proof = setup_fixture()
    plans = evaluate("TEST1.NS", stock, bench, proof, cfg, cal, now).plans
    model = ResearchResponse(market="NSE", vintage=proof.vintage, model_status="RESEARCH_ONLY",
                             paper_eligible=False, plans=list(plans), failed=[], filtered=[],
                             funnel={"plans": len(plans)}, missing_gates=["strategy_validation"])
    payload = model.model_dump(mode="json")
    assert payload["plans"][0]["entry_min"] > payload["plans"][0]["stop"]
    assert payload["plans"][0]["decision_at"].endswith("Z")
    assert payload["plans"][0]["quantity"] is None
    assert payload["plans"][0]["cash_required"] is None
    assert payload["plans"][0]["requested_risk_inr"] == 2500


@pytest.mark.asyncio
async def test_sqlite_and_hash_work_does_not_block_event_loop(isolated, monkeypatch):
    cfg, clock, report, app = isolated
    entered, release = threading.Event(), threading.Event()
    original = SwingResearchService.results
    def blocking(self, vintage):
        entered.set()
        assert release.wait(5), "Test did not release worker"
        return original(self, vintage)
    monkeypatch.setattr(SwingResearchService, "results", blocking)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        job = asyncio.create_task(client.get("/api/swing/results", params={"vintage": report["snapshot_id"]}))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            response = await asyncio.wait_for(client.get("/api/swing/status"), timeout=1)
            assert response.status_code == 200
        finally:
            release.set()
        assert (await job).status_code == 200

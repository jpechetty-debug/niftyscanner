"""SYNTHETIC S5 controls/outbox/reconciliation regression tests. No external POSTs."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app.core.config import Settings
from app.main import create_app
from app.market.clock import FakeClock
from app.swing.ledger import PaperConflict
from app.swing.operations import OperationsSettings, SwingOperations
from tests.fixtures.swing_replay import replay_fixture, capture, advance
from tests.fixtures.swing_source import create_source
from scripts.swing_data import run_import


def operations(fixture, root, url=""):
    cfg, cal, clock, plan, ledger, market, engine = fixture
    return SwingOperations(OperationsSettings(_env_file=None, SWING_OPS_DB_PATH=str(root/"ops.db"),
                           SWING_LIFECYCLE_WEBHOOK_URL=url), cfg, clock)


def test_control_revision_idempotency_rollback_and_read_without_creation(tmp_path):
    fixture = replay_fixture(tmp_path)
    ops = operations(fixture, tmp_path)
    assert ops.controls()["revision"] == 0 and not ops.path.exists()
    first = ops.change("pause", 0, {"paused": True})
    assert first == ops.change("pause", 0, {"paused": True})
    with pytest.raises(PaperConflict):
        ops.change("pause", 0, {"paused": False})
    with pytest.raises(PaperConflict):
        ops.change("wrong-revision", 0, {"paused": False})
    ops.change("resume", 1, {"paused": False})
    restored = ops.change("restore", 2, {"rollback_revision": 0})
    assert restored["paused"] and restored["alerts_paused"] and restored["revision"] == 3
    assert len(ops.history()) == 3
    with ops.connection(True) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM controls")


def test_controls_detect_corruption_and_refuse_unrelated_store(tmp_path):
    fixture = replay_fixture(tmp_path)
    ops = operations(fixture, tmp_path)
    ops.change("pause", 0, {"paused": True})
    with ops.connection(True) as conn:
        conn.execute("DROP TRIGGER immutable_controls_UPDATE")
        conn.execute("UPDATE controls SET hash='wrong'")
    with pytest.raises(PaperConflict, match="hash chain"):
        ops.controls()
    unknown = tmp_path/"unknown.db"
    with sqlite3.connect(unknown) as conn:
        conn.execute("CREATE TABLE other(value)")
    other = SwingOperations(OperationsSettings(_env_file=None, SWING_OPS_DB_PATH=str(unknown)), fixture[0], fixture[2])
    with pytest.raises(PaperConflict, match="refusing migration"):
        other.change("pause", 0, {"paused": True})


def test_operations_store_cannot_overlap_financial_or_source_evidence(tmp_path):
    fixture = replay_fixture(tmp_path)
    for path in (fixture[0].SWING_PAPER_DB_PATH, fixture[0].SWING_DB_PATH, "data/history.db", "data/swing_validation.db"):
        with pytest.raises(ValueError, match="separate"):
            SwingOperations(OperationsSettings(_env_file=None, SWING_OPS_DB_PATH=path), fixture[0], fixture[2])


def test_alerts_are_disabled_and_enabling_requires_dedicated_destination(tmp_path):
    fixture = replay_fixture(tmp_path)
    ops = operations(fixture, tmp_path)
    ops.collect(fixture[4])
    assert ops.dispatch(lambda *a, **k: pytest.fail("No external calls allowed")) == 0
    assert not ops.path.exists()
    with pytest.raises(PaperConflict, match="dedicated"):
        ops.change("enable", 0, {"alerts_paused": False})


def test_alert_opt_in_snapshots_old_heads_even_at_identical_clock_time(tmp_path):
    fixture = replay_fixture(tmp_path)
    capture(fixture)  # all old events share the exact injected time of opt-in
    ops = operations(fixture, tmp_path, "https://example.invalid/SYNTHETIC-hook")
    ops.change("enable", 0, {"alerts_paused": False})
    ops.collect(fixture[4])
    assert ops.outbox() == []
    first = fixture[3].first_entry_session
    fixture[5].day(fixture[3], first, high=fixture[3].target+1)
    advance(fixture, first)
    ops.collect(fixture[4])
    assert [r["lifecycle"] for r in ops.outbox()] == ["CLOSED", "OPEN"]


def test_durable_outbox_claims_unknown_delivery_and_preserves_ids_on_reviewed_retry(tmp_path):
    fixture = replay_fixture(tmp_path)
    ops = operations(fixture, tmp_path, "https://example.invalid/SYNTHETIC-hook")
    ops.change("enable", 0, {"alerts_paused": False})
    capture(fixture)
    ops.collect(fixture[4])
    original = ops.outbox()
    assert len(original) == 2
    ops.collect(fixture[4])
    assert ops.outbox() == original
    calls = []
    def uncertain(url, **kwargs):
        calls.append(kwargs)
        assert "SYNTHETIC" not in json.dumps(kwargs["json"])
        assert not {"cash", "capital", "quantity", "account_id"} & kwargs["json"].keys()
        raise TimeoutError("SYNTHETIC timeout")
    assert ops.dispatch(uncertain) == 0
    assert len(calls) == 2 and all(r["status"] == "UNKNOWN" for r in ops.outbox())
    restarted = operations(fixture, tmp_path, "https://example.invalid/SYNTHETIC-hook")
    assert restarted.dispatch(uncertain) == 0 and len(calls) == 2
    event_id = original[0]["event_id"]
    assert restarted.retry(event_id, "review") == restarted.retry(event_id, "review")
    def success(url, **kwargs):
        assert kwargs["headers"]["Idempotency-Key"] == event_id
        return SimpleNamespace(status_code=204)
    assert restarted.dispatch(success) == 1
    with pytest.raises(PaperConflict):
        restarted.retry(event_id, "cannot-resend-success")


def test_destination_changes_do_not_redirect_queued_private_events(tmp_path):
    fixture = replay_fixture(tmp_path)
    ops = operations(fixture, tmp_path, "https://example.invalid/first")
    ops.change("enable", 0, {"alerts_paused": False})
    capture(fixture)
    ops.collect(fixture[4])
    changed = operations(fixture, tmp_path, "https://example.invalid/other")
    assert changed.dispatch(lambda *a, **k: pytest.fail("Cannot redirect old events")) == 0
    assert all(not item["destination_matches"] for item in changed.outbox())


def test_management_only_cancels_known_unfilled_but_preserves_unknown_reservations(tmp_path):
    fixture = replay_fixture(tmp_path)
    cfg, cal, clock, plan, ledger, market, engine = fixture
    capture(fixture)
    version = ledger.state("SYNTHETIC")["version"]
    ledger.command("SYNTHETIC", "manage", version, {"management_only": True},
                   lambda conn, state, emit: engine.advance(conn, state, emit, "SYNTHETIC", plan.session, allow_new_entries=False))
    state = ledger.state("SYNTHETIC")
    assert state["orders"][plan.id]["lifecycle"] == "CANCELLED" and state["cash"] == 1000000
    assert state["reserved_cash"] == 0 and not state["positions"]
    unknown = replay_fixture(tmp_path/"unknown")
    capture(unknown)
    advance(unknown, unknown[3].first_entry_session)  # no observations => unknown path
    ledger, engine = unknown[4], unknown[6]
    before = ledger.state("SYNTHETIC")
    assert before["orders"][unknown[3].id]["lifecycle"] == "UNRESOLVED"
    ledger.command("SYNTHETIC", "manage-unknown", before["version"], {"management_only": True},
        lambda conn, state, emit: engine.advance(conn, state, emit, "SYNTHETIC", unknown[3].first_entry_session, allow_new_entries=False))
    after = ledger.state("SYNTHETIC")
    assert after["reserved_cash"] == before["reserved_cash"] > 0
    assert after["orders"][unknown[3].id]["lifecycle"] == "UNRESOLVED"


def test_management_only_continues_existing_holding_exits(tmp_path):
    fixture = replay_fixture(tmp_path)
    cfg, cal, clock, plan, ledger, market, engine = fixture
    capture(fixture)
    first = plan.first_entry_session
    market.day(plan, first)
    advance(fixture, first)
    assert ledger.state("SYNTHETIC")["positions"]
    second = cal.calendar.next_session(str(first)).date()
    market.day(plan, second, high=plan.target+1)
    clock.set_time(cal.calendar.session_close(str(second)).to_pydatetime()+timedelta(minutes=10))
    version = ledger.state("SYNTHETIC")["version"]
    ledger.command("SYNTHETIC", "manage-held", version, {"management_only": True},
        lambda conn, state, emit: engine.advance(conn, state, emit, "SYNTHETIC", second, allow_new_entries=False))
    state = ledger.state("SYNTHETIC")
    assert len(state["closed"]) == 1 and not state["positions"]


@pytest.fixture
def api_env(tmp_path):
    source, universe = create_source(tmp_path)
    cfg = Settings(_env_file=None, ENABLED_MARKETS="NSE", SWING_ENABLED=True, SWING_REPLAY_ENABLED=True,
        NSE_UNIVERSE_PATH=str(universe), SWING_MIN_HISTORY_SESSIONS=3, SWING_DELIVERY_SESSIONS=3,
        SWING_DB_PATH=str(tmp_path/"SYNTHETIC-observations.db"), SWING_PAPER_DB_PATH=str(tmp_path/"SYNTHETIC-ledger.db"),
        SWING_ARTIFACT_DIR=str(tmp_path/"SYNTHETIC-evidence"), PERFORMANCE_ENABLED=False)
    clock = FakeClock(datetime(2026, 10, 7, 3, tzinfo=timezone.utc))
    imported = run_import(cfg, clock, source)
    ops = OperationsSettings(_env_file=None, SWING_OPS_DB_PATH=str(tmp_path/"ops.db"))
    app = create_app(config=cfg, clock=clock, start_scheduler=False, operations_settings=ops)
    return cfg, clock, app, imported


def test_workspace_api_excludes_synthetic_charts_and_does_not_create_stores(api_env):
    cfg, clock, app, imported = api_env
    with TestClient(app) as client:
        assert client.get("/api/swing/vintages").json() == []
        assert client.get("/api/swing/chart", params={"vintage": imported["snapshot_id"], "ticker": "TEST1.NS"}).status_code == 409
        ops = client.get("/api/swing/operations").json()
        assert not ops["scheduler_running"] and not ops["outcomes_worker_running"]
        assert client.get("/api/swing/paper/accounts").json() == []
        assert not Path(cfg.SWING_PAPER_DB_PATH).exists()
        assert not app.state.swing_workspace.operations.path.exists()
        assert client.get("/api/swing/workspace", params={"vintage": "bad"}).status_code == 422
        assert client.get("/api/swing/vintages", params={"market": "NYSE"}).status_code == 404


def test_forward_monitor_reads_are_safe_and_have_no_write_endpoint(api_env):
    cfg, clock, app, imported = api_env
    from app.swing.forward import ForwardService
    from app.swing.forward_store import ForwardSettings
    from fastapi.testclient import TestClient
    path = Path(cfg.SWING_DB_PATH).parent/"SYNTHETIC-forward-monitor.db"
    app.state.forward = ForwardService(cfg, clock, app.state.calendars["NSE"],
        ForwardSettings(_env_file=None, SWING_FORWARD_DB_PATH=str(path)), app.state.paper)
    with TestClient(app) as client:
        assert client.get("/api/swing/forward/campaigns").json() == []
        assert client.get("/api/swing/forward/campaigns/unknown").status_code == 404
        assert client.get("/api/swing/forward/campaigns", params={"market": "NYSE"}).status_code == 404
        assert client.get("/api/swing/forward/campaigns", params={"market": "INVALID"}).status_code == 422
        assert client.post("/api/swing/forward/campaigns", json={}).status_code == 405
    assert not path.exists() and not Path(cfg.SWING_PAPER_DB_PATH).exists()


def test_pause_stops_new_jobs_but_management_jobs_can_drain(api_env):
    import asyncio
    cfg, clock, app, imported = api_env
    with TestClient(app) as client:
        account = {"account_id": "SYNTHETIC", "capital_inr": "1000000.00", "session": "2026-10-05", "key": "fund"}
        assert client.post("/api/swing/paper/accounts", json=account).status_code == 200
        body = {"account_id": "SYNTHETIC", "vintage": imported["snapshot_id"], "through": "2026-10-06", "expected_version": 1, "key": "normal"}
        queued = client.post("/api/swing/replay", json=body).json()
        pause = {"key": "pause", "expected_revision": 0, "paused": True}
        assert client.post("/api/swing/operations", json=pause).status_code == 200
        assert client.post("/api/swing/operations", json=pause).json()["revision"] == 1
        assert client.post("/api/swing/replay", json=body).status_code == 202  # original receipt still available
        assert client.post("/api/swing/replay", json={**body, "key": "new"}).status_code == 409
        assert client.post("/api/swing/paper/accounts", json=account).status_code == 200
        assert app.state.paper.next_job() is None
        managed = client.post("/api/swing/replay", json={**body, "key": "manage", "management_only": True})
        assert managed.status_code == 202
        asyncio.run(app.state.scheduler.evaluate_swing_if_idle())
        assert app.state.paper.ledger.job(managed.json()["id"])["status"] == "DONE"
        assert app.state.paper.ledger.job(queued["id"])["status"] == "QUEUED"


@pytest.mark.asyncio
async def test_pause_does_not_acknowledge_while_shared_worker_is_busy(api_env):
    import httpx
    _, _, app, _ = api_env
    async with app.state.scheduler.state.scan_lock:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.post("/api/swing/operations", json={"key": "pause", "expected_revision": 0, "paused": True})
            assert result.status_code == 429 and result.headers["Retry-After"]
            assert app.state.swing_workspace.operations.controls()["revision"] == 0

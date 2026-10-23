"""SYNTHETIC worker timing; injected clocks/timers, no market/network calls."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.market.clock import FakeClock
from app.core.outcomes import FunnelCounts
from app.scheduler.metrics import WorkerTimings, queue_age_seconds
from app.swing.ledger import PaperLedger
from tests.fixtures.swing_replay import replay_fixture
from tests.test_repairs import setup_scheduler
from tests.test_swing_operations import api_env


class Timer:
    value = 0.

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class SignalledLock:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.requested = asyncio.Event()

    async def __aenter__(self):
        self.requested.set()
        await self.lock.acquire()

    async def __aexit__(self, *args):
        self.lock.release()


def collector():
    clock = FakeClock(datetime(2026, 10, 1, 6, tzinfo=timezone.utc))
    timer = Timer()
    return clock, timer, WorkerTimings(clock, ["SYNTHETIC"], timer)


@pytest.mark.parametrize("stamp", [None, "invalid", "2026-10-01T05:00:00", "2026-10-02T06:00:00+00:00"])
def test_unknown_queue_age_is_not_zero(stamp):
    clock, _, _ = collector()
    assert queue_age_seconds(stamp, clock.now()) is None


def test_fresh_collector_and_snapshot_copies():
    clock, timer, metrics = collector()
    metrics.deferred("SYNTHETIC")
    metrics.preflight_failed("SYNTHETIC")
    row = metrics.snapshot()["workers"][0]
    assert row["attempts"] == 0 and row["busy_deferrals"] == 1 and row["preflight_failures"] == 1
    assert row["active"] is row["last"] is row["max_duration_sec"] is None
    row["outcomes"]["failed"] = 99
    assert metrics.snapshot()["workers"][0]["outcomes"]["failed"] == 0
    assert WorkerTimings(clock, ["SYNTHETIC"], timer).snapshot()["workers"][0]["busy_deferrals"] == 0


@pytest.mark.asyncio
async def test_wait_runtime_and_queue_age_use_separate_clocks():
    clock, timer, metrics = collector()
    lock, entered, finish = SignalledLock(), asyncio.Event(), asyncio.Event()
    queued = clock.now()-timedelta(seconds=30)
    await lock.lock.acquire()

    async def work():
        async with metrics.measure_lock("SYNTHETIC", lock, queued_at=queued, queue_source="durable_job"):
            metrics.progress("SYNTHETIC", "SYNTHETIC_processing")
            entered.set()
            await finish.wait()

    task = asyncio.create_task(work())
    await lock.requested.wait()
    clock.sleep(10)
    timer.advance(3)
    waiting = metrics.snapshot()["workers"][0]
    assert waiting["state"] == "waiting"
    assert waiting["active"]["current_wait_sec"] == 3
    assert waiting["active"]["queue_age_sec"] == 40
    lock.lock.release()
    await entered.wait()
    clock.set_time(clock.now()-timedelta(hours=1))
    timer.advance(5)
    running = metrics.snapshot()["workers"][0]["active"]
    assert running["lock_wait_sec"] == 3 and running["duration_sec"] == 5
    assert running["queue_age_sec"] == 40 and running["outcome"] is None
    assert running["phase"] == "SYNTHETIC_processing"
    finish.set()
    await task
    row = metrics.snapshot()["workers"][0]
    assert row["active"] is None and row["last"]["outcome"] == "completed"
    assert row["max_duration_sec"] == 5 and row["max_lock_wait_sec"] == 3
    assert row["outcomes"]["completed"] == 1 and not lock.lock.locked()


@pytest.mark.asyncio
async def test_cancellation_while_waiting_keeps_owner_lock_and_no_task_duration():
    _, timer, metrics = collector()
    lock = SignalledLock()
    await lock.lock.acquire()

    async def work():
        async with metrics.measure_lock("SYNTHETIC", lock):
            pytest.fail("Cancelled waiter must never run")

    task = asyncio.create_task(work())
    await lock.requested.wait()
    timer.advance(4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = metrics.snapshot()["workers"][0]
    assert row["last"]["outcome"] == "cancelled" and row["outcomes"]["cancelled"] == 1
    assert row["last"]["lock_wait_sec"] == 4 and row["last"]["duration_sec"] is None
    assert row["active"] is None and lock.lock.locked()
    lock.lock.release()


@pytest.mark.asyncio
async def test_worker_failure_propagates_and_releases_lock():
    _, timer, metrics = collector()
    lock = asyncio.Lock()
    with pytest.raises(RuntimeError, match="SYNTHETIC failure"):
        async with metrics.measure_lock("SYNTHETIC", lock):
            timer.advance(6)
            raise RuntimeError("SYNTHETIC failure")
    row = metrics.snapshot()["workers"][0]
    assert row["last"]["outcome"] == "failed" and row["last"]["duration_sec"] == 6
    assert not lock.locked() and row["outcomes"]["failed"] == 1


@pytest.mark.asyncio
async def test_scan_duration_includes_universe_and_persistence(monkeypatch):
    _, clock, state, scheduler, scanners = setup_scheduler()
    timer = Timer()
    scheduler.worker_timings = WorkerTimings(clock, ["scan:NSE"], timer)
    scheduler._scan_requests["NSE"] = (clock.now()-timedelta(seconds=20), "scheduled_deadline")
    universe = scheduler.universes["NSE"]
    load = universe.load

    def timed_load():
        timer.advance(2)
        return load()

    def scan(*args, **kwargs):
        timer.advance(7)
        return [], FunnelCounts(), [], 0, 7., True

    monkeypatch.setattr(universe, "load", timed_load)
    scanners["NSE"].run_scan.side_effect = scan
    monkeypatch.setattr(state, "persist_last_scan", lambda *a, **kw: timer.advance(3))
    assert await scheduler.execute_scan("NSE")
    last = scheduler.worker_timings.snapshot()["workers"][0]["last"]
    assert last["duration_sec"] == 12 and last["queue_age_sec"] == 20
    assert last["queue_source"] == "scheduled_deadline" and last["outcome"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,outcome", [("DONE", "completed"), ("FAILED", "failed"), ("PAUSED", "paused")])
async def test_replay_measures_durable_queue_and_preserves_return_status(status, outcome):
    cfg, clock, state, scheduler, _ = setup_scheduler()
    cfg.SWING_ENABLED = cfg.SWING_REPLAY_ENABLED = True
    timer = Timer()
    scheduler.worker_timings = WorkerTimings(clock, ["replay"], timer)
    scheduler.operations = None
    job = {"id": "SYNTHETIC", "created_at": (clock.now()-timedelta(seconds=60)).isoformat()}
    def process(value):
        assert value == job
        timer.advance(8)
        return {"status": status}
    scheduler.paper = Mock()
    scheduler.paper.next_job.return_value = job
    scheduler.paper.process.side_effect = process
    await state.scan_lock.acquire()
    await scheduler.evaluate_swing_if_idle()
    scheduler.paper.next_job.assert_not_called()
    state.scan_lock.release()
    await scheduler.evaluate_swing_if_idle()
    row = scheduler.worker_timings.snapshot()["workers"][0]
    assert row["busy_deferrals"] == 1 and row["attempts"] == 1
    assert row["last"]["queue_age_sec"] == 60 and row["last"]["duration_sec"] == 8
    assert row["last"]["outcome"] == outcome and row["outcomes"][outcome] == 1


def test_durable_queue_survives_restart_excludes_finished_and_failed(tmp_path):
    _, _, clock, plan, ledger, _, _ = replay_fixture(tmp_path)
    request = {"vintage": "SYNTHETIC", "through": str(plan.session), "expected_version": 1}
    first = ledger.enqueue("SYNTHETIC", "one", request)
    created = ledger.job(first)["created_at"]
    clock.sleep(10)
    second = ledger.enqueue("SYNTHETIC", "two", {**request, "management_only": True})
    recovered = PaperLedger(ledger.path, clock)
    queue = recovered.queue_summary()
    assert queue["pending"] == 2 and queue["management_only_pending"] == 1
    assert queue["oldest_queued_at"] == created and queue_age_seconds(created, clock.now()) == 10
    ledger.command("SYNTHETIC", "one", 1, request,
                   lambda conn, state, emit: emit("REPLAY_RECORDED", {"SYNTHETIC": True}))
    assert recovered.queue_summary()["pending"] == 1
    ledger.record_job_error(second, "SYNTHETIC_FAILURE")
    assert recovered.queue_summary()["pending"] == 0
    assert recovered.queue_summary()["oldest_queued_at"] is None


def test_absent_queue_is_not_created(tmp_path):
    ledger = PaperLedger(tmp_path/"SYNTHETIC-missing.db", collector()[0])
    assert ledger.queue_summary()["status"] == "absent"
    assert not ledger.path.exists()


@pytest.mark.asyncio
async def test_manual_acceptance_wait_and_cross_market_rejection():
    _, clock, _, scheduler, _ = setup_scheduler("NSE,NYSE")
    assert scheduler.trigger_immediate_scan("NSE")[0]
    assert not scheduler.trigger_immediate_scan("NYSE")[0]
    # A scheduler wake/startup cannot relabel an already accepted manual task.
    scheduler._stop_event.set()
    await scheduler._run_loop()
    clock.sleep(4)
    await asyncio.gather(*scheduler._bg_tasks)
    rows = {r["worker"]: r for r in scheduler.worker_timings.snapshot()["workers"]}
    assert rows["scan:NSE"]["last"]["queue_age_sec"] == 4
    assert rows["scan:NSE"]["last"]["queue_source"] == "manual_acceptance"
    assert rows["scan:NYSE"]["attempts"] == 0


@pytest.mark.asyncio
async def test_startup_queue_age_includes_prior_market_scan():
    _, clock, _, scheduler, scanners = setup_scheduler("NSE,NYSE")
    scheduler.calendars = {m: Mock(is_market_open=Mock(return_value=True)) for m in scanners}
    def first(_):
        clock.sleep(9)
        return [], FunnelCounts(), [], 0, 9., True
    def second(_):
        scheduler._stop_event.set()
        return [], FunnelCounts(), [], 0, 0., True
    scanners["NSE"].run_scan.side_effect = first
    scanners["NYSE"].run_scan.side_effect = second
    await scheduler._run_loop()
    rows = {r["worker"]: r for r in scheduler.worker_timings.snapshot()["workers"]}
    assert rows["scan:NSE"]["last"]["queue_age_sec"] == 0
    assert rows["scan:NYSE"]["last"]["queue_age_sec"] == 9
    assert rows["scan:NYSE"]["last"]["queue_source"] == "startup"


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", [False, True])
async def test_outcomes_and_lifecycle_timings_cover_existing_work(failing):
    cfg, clock, state, scheduler, _ = setup_scheduler()
    cfg.SWING_ENABLED = cfg.PERFORMANCE_ENABLED = True
    timer = Timer()
    scheduler.worker_timings = WorkerTimings(clock, ["outcomes", "lifecycle_alerts"], timer)
    scheduler.calendars = {}
    def outcomes():
        timer.advance(5)
        if failing:
            raise RuntimeError("SYNTHETIC failure")
        return False
    scheduler.performance = Mock(is_due=Mock(return_value=True), run_if_due=Mock(side_effect=outcomes))
    def collect(_):
        timer.advance(2)
    def dispatch():
        timer.advance(3)
        if failing:
            raise RuntimeError("SYNTHETIC failure")
    scheduler.paper = Mock()
    scheduler.swing_operations = Mock(webhook_configured=True, collect=Mock(side_effect=collect), dispatch=Mock(side_effect=dispatch))
    await scheduler.evaluate_performance_if_idle()
    await scheduler.deliver_swing_alerts_if_idle()
    rows = {r["worker"]: r for r in scheduler.worker_timings.snapshot()["workers"]}
    assert rows["outcomes"]["last"]["outcome"] == ("failed" if failing else "skipped")
    assert rows["lifecycle_alerts"]["last"]["outcome"] == ("failed" if failing else "completed")
    for row in rows.values():
        assert row["last"]["duration_sec"] == 5 and row["last"]["queue_age_sec"] is None
    assert not state.scan_lock.locked()


def test_operations_exposes_fresh_metrics_without_starting_workers(api_env, monkeypatch):
    cfg, clock, app, _ = api_env
    with TestClient(app) as client:
        response = client.get("/api/swing/operations")
        assert response.status_code == 200
        metrics = response.json()["worker_timings"]
        assert metrics["scope"] == "scheduler_instance" and not metrics["lock_locked"]
        assert metrics["replay_queue"]["status"] == "absent"
        assert metrics["replay_queue"]["oldest_age_sec"] is None
        for row in metrics["workers"]:
            assert row["attempts"] == 0 and row["last"] is None and row["max_duration_sec"] is None
        assert not PaperLedger(cfg.SWING_PAPER_DB_PATH, clock).path.exists()
        monkeypatch.setattr(PaperLedger, "queue_summary", Mock(return_value={
            "status": "available", "pending": 2, "management_only_pending": 1,
            "oldest_queued_at": (clock.now()-timedelta(seconds=75)).isoformat()}))
        queue = client.get("/api/swing/operations").json()["worker_timings"]["replay_queue"]
        assert queue["oldest_age_sec"] == 75 and queue["pending"] == 2 and queue["age_status"] == "available"
        monkeypatch.setattr(PaperLedger, "queue_summary", Mock(return_value={
            "status": "available", "pending": 1, "oldest_queued_at": "invalid SYNTHETIC timestamp"}))
        queue = client.get("/api/swing/operations").json()["worker_timings"]["replay_queue"]
        assert queue["oldest_age_sec"] is None and queue["age_status"] == "timestamp_unavailable"
        monkeypatch.setattr(PaperLedger, "queue_summary", Mock(side_effect=OSError("SYNTHETIC unreadable")))
        payload = client.get("/api/swing/operations").json()
        assert payload["worker_timings"]["replay_queue"]["status"] == "unavailable"
        assert "controls" in payload

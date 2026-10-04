"""SYNTHETIC regression tests for incremental outcome sync (fictitious TEST tickers only)."""
from datetime import timedelta

import pytest

from app.cache.persistence import save_last_scan
from app.performance.service import PerformanceService
from tests.test_performance import SyntheticPrices, capture, setup  # noqa: F401  (fixture re-export)


def _cohort(service):
    with service.repository.connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM signal_cohorts ORDER BY id")]


def _resolve(setup):
    assert setup[3].run_if_due()


def test_late_earlier_capture_after_resolution_does_not_reattribute_history(setup):
    capture(setup, timestamp="2026-10-05T11:00:00+00:00", score=0.8)   # enters Tue Oct 6
    _resolve(setup)
    before = setup[3].report(horizon=1)["outcomes"][0]
    assert before["entry_session"] == "2026-10-06" and before["status"] == "resolved"

    capture(setup, timestamp="2026-10-01T11:00:00+00:00", score=0.2)   # earlier, arrives late
    after = setup[3].report(horizon=1)["outcomes"][0]
    for key in ("signal_id", "score", "entry_session", "exit_session", "excess_return", "status"):
        assert after[key] == before[key], key
    assert len(_cohort(setup[3])) == 1


def test_late_earlier_capture_before_resolution_repoints_entry_and_exits(setup):
    capture(setup, timestamp="2026-10-05T11:00:00+00:00", score=0.8)
    setup[3].report(horizon=1)                                          # ingest, nothing resolved yet
    capture(setup, timestamp="2026-10-01T11:00:00+00:00", score=0.2)
    rows = setup[3].report(horizon=1)["outcomes"]
    assert len(rows) == 1
    assert rows[0]["score"] == 0.2 and rows[0]["entry_session"] == "2026-10-05"
    assert rows[0]["exit_session"] == "2026-10-05"                      # one-session trade: enter and exit same day
    five = setup[3].report(horizon=5)["outcomes"][0]
    assert five["exit_session"] == "2026-10-09"


def test_sync_is_incremental_and_unchanged_context_does_no_outcome_scan(setup):
    capture(setup)
    setup[3].report()
    statements = []
    with setup[3].repository.connection() as conn:
        conn.set_trace_callback(statements.append)
        setup[3].sync(conn)                                             # nothing new
        conn.set_trace_callback(None)
    touched = [s for s in statements if "signal_outcomes" in s or "signal_cohorts" in s]
    assert touched == [], touched


def test_chunked_sync_matches_single_pass_and_cursor_advances_with_data(setup, monkeypatch):
    for i in range(7):
        capture(setup, ticker=f"TEST{i}.NS", timestamp=f"2026-10-01T11:{i:02d}:00+00:00")
    monkeypatch.setattr(PerformanceService, "SYNC_CHUNK", 2)
    setup[3].report()
    with setup[3].repository.connection() as conn:
        cursor = conn.execute("SELECT last_signal_id FROM performance_sync").fetchone()[0]
        assert cursor == conn.execute("SELECT max(id) FROM signals").fetchone()[0]
        assert conn.execute("SELECT count(*) FROM signal_cohorts").fetchone()[0] == 7
        assert conn.execute("SELECT count(*) FROM signal_outcomes").fetchone()[0] == 21


def test_new_benchmark_context_initialises_from_canonical_schedule_once(setup):
    capture(setup)
    setup[3].report()
    setup[0].PERFORMANCE_BENCHMARK = "SYNTHETIC-OTHER-INDEX"
    report = setup[3].report(horizon=5)
    row = report["outcomes"][0]
    assert row["benchmark"] == "SYNTHETIC-OTHER-INDEX" and row["status"] in ("pending", "unresolved")
    assert row["exit_session"] == "2026-10-09"                          # derived from entry, not copied
    with setup[3].repository.connection() as conn:
        assert conn.execute("SELECT count(*) FROM performance_contexts").fetchone()[0] == 2


def test_due_check_happens_before_ingest_and_scan_lock(setup, monkeypatch):
    capture(setup)
    _resolve(setup)
    assert setup[3].is_due() is False                                   # job complete for tonight
    monkeypatch.setattr(PerformanceService, "sync", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sync ran")))
    assert setup[3].run_if_due() is False                               # guard returns before any ingest


def test_healthy_continuation_resumes_next_tick_but_failure_backs_off(setup):
    setup[0].PERFORMANCE_BATCH_SIZE = 1
    for i in range(3):
        capture(setup, ticker=f"TEST{i}.NS")
    assert setup[3].run_if_due()
    with setup[3].repository.connection() as conn:
        job = dict(conn.execute("SELECT * FROM performance_jobs").fetchone())
    assert job["status"] == "continuing" and job["retry_at"] is None
    assert setup[3].run_if_due()                                        # next tick, no 300 s wait

    setup[2].fail = True
    assert setup[3].run_if_due()
    with setup[3].repository.connection() as conn:
        job = dict(conn.execute("SELECT * FROM performance_jobs").fetchone())
    assert job["status"] == "failed" and job["retry_at"] is not None
    assert setup[3].run_if_due() is False                               # backoff honoured


def test_scheduler_does_not_take_scan_slot_when_nothing_due():
    import asyncio
    from app.scheduler.runner import Scheduler
    from app.core.config import Settings

    class Perf:
        def is_due(self): return False
        def run_if_due(self): raise AssertionError("must not run")

    class State:
        scan_lock = asyncio.Lock()

    sched = Scheduler.__new__(Scheduler)
    sched.performance, sched.state, sched._reserved_market, sched.calendars = Perf(), State(), None, {}
    asyncio.run(sched.evaluate_performance_if_idle())
    assert not State.scan_lock.locked()


def test_returning_to_previous_context_includes_newer_cohorts(setup):
    capture(setup)
    setup[3].report()
    original = setup[0].PERFORMANCE_BENCHMARK
    setup[0].PERFORMANCE_BENCHMARK = "SYNTHETIC-OTHER-INDEX"
    setup[3].report()
    capture(setup, ticker="TEST2.NS")
    assert len(setup[3].report()["outcomes"]) == 2
    setup[0].PERFORMANCE_BENCHMARK = original
    assert {r["ticker"] for r in setup[3].report()["outcomes"]} == {"TEST1.NS", "TEST2.NS"}


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["reservation", "scan", "shutdown"])
async def test_preflight_rechecks_scan_priority(interruption):
    import asyncio
    import threading
    from types import SimpleNamespace
    from app.scheduler.runner import Scheduler

    loop, changed = asyncio.get_running_loop(), threading.Event()
    sched = Scheduler.__new__(Scheduler)
    sched.state = SimpleNamespace(scan_lock=asyncio.Lock())
    sched._reserved_market, sched._stopping, sched.calendars = None, False, {}

    async def interrupt():
        if interruption == "reservation":
            sched._reserved_market = "NSE"
        elif interruption == "scan":
            await sched.state.scan_lock.acquire()
        else:
            sched._stopping = True
        changed.set()

    class Perf:
        def is_due(self):
            asyncio.run_coroutine_threadsafe(interrupt(), loop)
            assert changed.wait(2)
            return True

        def run_if_due(self):
            raise AssertionError("scan priority was lost")

    sched.performance = Perf()
    # The scheduler catches job exceptions, so track invocation explicitly.
    calls = []
    sched.performance.run_if_due = lambda: calls.append(True)
    try:
        await asyncio.wait_for(sched.evaluate_performance_if_idle(), 1)
        assert calls == []
    finally:
        if sched.state.scan_lock.locked():
            sched.state.scan_lock.release()


def test_chunk_failure_preserves_committed_cursor_and_resumes(setup, monkeypatch):
    for i in range(5):
        capture(setup, ticker=f"TEST{i}.NS")
    setup[0].PERFORMANCE_SYNC_CHUNK = 2
    service = setup[3]
    original = service._ingest

    def fail(conn, row):
        if row["ticker"] == "TEST3.NS":
            raise RuntimeError("SYNTHETIC ingest interruption")
        original(conn, row)

    monkeypatch.setattr(service, "_ingest", fail)
    with pytest.raises(RuntimeError, match="ingest interruption"):
        service.report()
    with service.repository.connection() as conn:
        assert conn.execute("SELECT last_signal_id FROM performance_sync").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM signal_cohorts").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM signals").fetchone()[0] == 5
    monkeypatch.setattr(service, "_ingest", original)
    assert len(service.report()["outcomes"]) == 5


@pytest.mark.parametrize("operation", ["is_due", "report"])
def test_unchanged_reads_during_another_writer(setup, operation):
    from concurrent.futures import ThreadPoolExecutor

    capture(setup)
    setup[3].report()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with setup[3].repository.connection() as writer:
            writer.execute("BEGIN IMMEDIATE")
            try:
                assert executor.submit(getattr(setup[3], operation)).result(timeout=2)
            finally:
                writer.rollback()


def test_parallel_context_sync_keeps_cursor_and_all_cohorts(setup):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    setup[0].PERFORMANCE_SYNC_CHUNK = 2
    for i in range(20):
        capture(setup, ticker=f"TEST{i}.NS")
    second_config = setup[0].model_copy(update={"PERFORMANCE_BENCHMARK": "SYNTHETIC-OTHER-INDEX"})
    second = PerformanceService(second_config, setup[1], setup[3].calendar, setup[2])
    ready = threading.Barrier(2)

    def report(service):
        ready.wait(timeout=2)
        return service.report()

    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = [executor.submit(report, service) for service in (setup[3], second)]
        assert all(len(future.result(timeout=5)["outcomes"]) == 20 for future in reports)
    capture(setup, ticker="TEST20.NS")
    assert len(second.report()["outcomes"]) == 21
    assert len(setup[3].report()["outcomes"]) == 21
    with setup[3].repository.connection() as conn:
        assert conn.execute("SELECT last_signal_id FROM performance_sync").fetchone()[0] == 21
        assert conn.execute("SELECT count(*) FROM signal_outcomes").fetchone()[0] == 126

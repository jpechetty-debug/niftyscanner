"""Bounded observational timing for existing workers; never schedules work."""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from time import perf_counter
from typing import Callable

from loguru import logger


def queue_age_seconds(created_at: str | datetime | None, now: datetime) -> float | None:
    """Unknown/invalid/future enqueue timestamps must not look like a zero wait."""
    try:
        stamp = datetime.fromisoformat(created_at) if isinstance(created_at, str) else created_at
        if stamp is None or stamp.tzinfo is None or stamp.utcoffset() is None:
            return None
        elapsed = (now-stamp).total_seconds()
        return elapsed if elapsed >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass
class _Attempt:
    requested_at: datetime
    requested_tick: float
    queued_at: str | datetime | None
    queue_source: str
    started_at: datetime | None = None
    started_tick: float | None = None
    queue_age_sec: float | None = None
    lock_wait_sec: float | None = None
    phase: str = "waiting_for_lock"
    outcome: str = "completed"


@dataclass
class _Worker:
    attempts: int = 0
    busy_deferrals: int = 0
    preflight_failures: int = 0
    last_deferred_at: datetime | None = None
    last_progress_at: datetime | None = None
    max_duration_sec: float | None = None
    max_lock_wait_sec: float | None = None
    outcomes: dict = field(default_factory=lambda: dict.fromkeys(("completed", "failed", "cancelled", "skipped", "paused"), 0))
    active: _Attempt | None = None
    last: dict | None = None


class WorkerTimings:
    """One current/last attempt per fixed worker, safe for off-loop status reads."""
    def __init__(self, clock, workers, timer: Callable[[], float] = perf_counter):
        self.clock, self.timer = clock, timer
        self.started_at = clock.now()
        self._workers = {key: _Worker() for key in workers}
        self._guard = RLock()

    def deferred(self, worker: str) -> None:
        with self._guard:
            row = self._workers[worker]
            row.busy_deferrals += 1
            row.last_deferred_at = self.clock.now()

    def preflight_failed(self, worker: str) -> None:
        with self._guard:
            self._workers[worker].preflight_failures += 1

    def progress(self, worker: str, phase: str) -> None:
        with self._guard:
            row = self._workers[worker]
            if row.active is not None:
                row.active.phase = phase
                row.last_progress_at = self.clock.now()

    @staticmethod
    def _view(attempt, now, tick, *, finished=False):
        return {"queued_at": attempt.queued_at, "queue_source": attempt.queue_source,
            "requested_at": attempt.requested_at, "started_at": attempt.started_at,
            "queue_age_sec": attempt.queue_age_sec if attempt.started_at is not None else queue_age_seconds(attempt.queued_at, now),
            "lock_wait_sec": attempt.lock_wait_sec,
            "current_wait_sec": max(0., tick-attempt.requested_tick) if attempt.started_tick is None else None,
            "duration_sec": max(0., tick-attempt.started_tick) if attempt.started_tick is not None else None,
            "phase": attempt.phase, "outcome": attempt.outcome if finished else None}

    def snapshot(self) -> dict:
        now, tick = self.clock.now(), self.timer()
        with self._guard:
            rows = []
            for name, worker in self._workers.items():
                rows.append({"worker": name, "state": "idle" if worker.active is None else
                    "waiting" if worker.active.started_at is None else "running",
                    "attempts": worker.attempts, "busy_deferrals": worker.busy_deferrals,
                    "preflight_failures": worker.preflight_failures, "last_deferred_at": worker.last_deferred_at,
                    "last_progress_at": worker.last_progress_at, "max_duration_sec": worker.max_duration_sec,
                    "max_lock_wait_sec": worker.max_lock_wait_sec, "outcomes": dict(worker.outcomes),
                    "active": self._view(worker.active, now, tick) if worker.active else None,
                    "last": dict(worker.last) if worker.last else None})
        return {"observed_at": now, "collection_started_at": self.started_at,
                "scope": "scheduler_instance", "workers": rows}

    @asynccontextmanager
    async def measure_lock(self, worker: str, lock, *, queued_at=None, queue_source="unavailable"):
        attempt = _Attempt(self.clock.now(), self.timer(), queued_at, queue_source)
        with self._guard:
            row = self._workers[worker]
            row.attempts += 1
            row.active = attempt
            row.last_progress_at = attempt.requested_at
        try:
            async with lock:
                with self._guard:
                    attempt.started_at, attempt.started_tick = self.clock.now(), self.timer()
                    attempt.lock_wait_sec = max(0., attempt.started_tick-attempt.requested_tick)
                    attempt.queue_age_sec = queue_age_seconds(queued_at, attempt.started_at)
                    attempt.phase = "running"
                    row.last_progress_at = attempt.started_at
                yield attempt
        except BaseException as exc:
            import asyncio
            attempt.outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            raise
        finally:
            now, tick = self.clock.now(), self.timer()
            with self._guard:
                last = self._view(attempt, now, tick, finished=True)
                last["finished_at"] = now
                last["current_wait_sec"] = None
                if attempt.started_tick is None:
                    last["lock_wait_sec"] = max(0., tick-attempt.requested_tick)
                row.last, row.active, row.last_progress_at = last, None, now
                row.outcomes[attempt.outcome] += 1
                for name in ("duration_sec", "lock_wait_sec"):
                    value = last[name]
                    if value is not None:
                        old = getattr(row, "max_"+name)
                        setattr(row, "max_"+name, max(old, value) if old is not None else value)
            logger.bind(worker=worker).info("Worker timing: outcome={} queue_age_sec={} lock_wait_sec={} duration_sec={}",
                attempt.outcome, last["queue_age_sec"], last["lock_wait_sec"], last["duration_sec"])

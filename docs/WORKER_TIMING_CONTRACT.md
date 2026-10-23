# Worker timing and queue visibility

Authorized measurement-only phase, 22 October 2026. Preserve the single global
scan lock, priority, deadlines, cooldown, pause behavior, durable job identities,
idempotence and shutdown draining. No new background task or automatic enabling.

Instrument existing NSE/NYSE scans, nightly outcome attempts, replay processing
and lifecycle collection/delivery. Measure actual lock acquisition wait separately
from busy skips. Count busy deferrals without claiming that a skipped poll proves
a queued job exists. Record last attempt, live wait/runtime, last progress stage,
outcome counts and maxima in bounded per-worker process-local state. No unbounded
per-job history, account/cash/secret labels or persistence migration.

Use the injected Clock for aware timestamps and queue ages, and an injectable
monotonic timer for elapsed lock wait and task duration. Duration includes the
existing off-loop work under that worker's lock, including persistence/legacy
notification for scans. Queue age at start uses explicit accepted/manual/startup/
scheduled-deadline timestamps or a replay job's durable created_at. Unknown,
naive or future timestamps are unavailable, not zero. Outcome completion means a
worker attempt completed; it is not proof all notifications/outcomes succeeded.
Paused/failed replay returns remain distinct from completed attempts.

Operations HTTP gains optional timing metadata, durable pending replay count and
oldest age, even while workers are paused/stopped. Queue reads are read-only and
never initialize missing stores; absent/unavailable/invalid timestamp states stay
explicit. Replay job responses expose existing created_at without rewriting jobs.
Lifecycle outbox schema has no enqueue timestamp: its age remains unavailable;
never infer enqueue age from its market-session date or delivery claim time.

The Operations UI displays API values only and supports old responses without
metrics. Fresh collectors show no measured durations. Counters/last samples reset
on a new Scheduler instance; replay age is recovered from its durable queue.
No load/stress/production latency claim without real measurements. Tests use fake
Clock/timer and isolated fixtures, with no sleeps or real provider/alert calls.

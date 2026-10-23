# Worker timing implementation and verification

22 October 2026. One authorized measurement phase, based on improvement 6 in
`GRAPH_IMPROVEMENT_REVIEW.md`. Reread `SPEC.md` before implementation. No blocker
required user clarification. Plan: trace existing lock/queue paths; define timing
boundaries; instrument existing workers; expose read-only Operations metadata;
verify clocks, failures, priority and durable queue reads.

## Result and measurement boundaries

The existing scan, outcome, replay and lifecycle workers now report current/last
attempts, monotonic acquisition wait/runtime, queue age at start when known,
progress stage/time, outcome counts, maxima, busy deferrals and preflight errors.
One current and one last attempt per fixed worker bound memory. Counters reset
with each Scheduler instance; they are not a historical monitoring service.

Queue time originates at manual acceptance, startup acceptance or scheduled due
time for scans, and durable job `created_at` for replay. Queue age freezes when
work acquires the lock. The separate replay queue summary reports pending count,
management-only count and current oldest age without starting a worker or
initializing a missing ledger. Completed/failed jobs leave that pending count.
Missing/invalid/future/naive timestamps are unavailable, never zero.

Busy skips remain skips, counted separately from lock acquisition. A busy poll
does not prove a job was queued. Normal operation often skips a locked worker
before acquisition, so a small acquisition wait alone cannot establish that a
worker experienced no delay. Startup/deadline/replay queue ages supply separate
context. Outcomes have no durable enqueue timestamp. Lifecycle alert payload
session dates and claim times are not enqueue times, so alert queue age is
explicitly unavailable. No alerts table migration was added.

Task duration includes existing work inside that worker's lock, including
universe loading, provider/scanner work, scan persistence and best-effort legacy
notification. It differs from existing scanner-only `scan_seconds`. A completed
worker attempt does not attest every downstream notification or outcome.
Returned FAILED/PAUSED replay states remain distinct. Cancellation records the
coroutine lifetime and propagates; it does not attest that a cancelled off-loop
thread has stopped. Existing graceful shutdown still drains work.

Operations gets additive optional `worker_timings` metadata. UI reads only those
HTTP values and supports older responses. Queue age, acquisition wait, duration,
maxima, attempts, busy deferrals, preflight failures, phase and progress time are
displayed in a snapshot with its observation time and refresh instruction.
Unmeasured durations stay blank/unavailable. No polling timer, provider request,
new worker, automatic enabling, scheduler ordering, deadline or fairness change.
Optional queue-read errors expose unavailable queue health; existing control and
outbox validation are not bypassed. Queue metadata is operational, not account
state/provenance attestation. No account/secret labels or unbounded job history.

## Graph navigation

Reused Graphify's 1,799-node/5,876-edge map; no rebuild. Expanded against its
vocabulary to `worker queue lock duration scheduler replay operations task jobs`,
then used DFS with a 1,400-token budget. The broad traversal was truncated, so
it served as navigation only. Token budget: 1,400; no claim of measured model
token usage. The graph predates these edits. CodeGraph supplied current source
and caller paths before edits to Scheduler, ledger, Operations and UI, plus their
tests. Current source establishes shared-lock behavior; no unobserved graph edge
is treated as runtime proof. Graphify reflection/lessons were consulted and the
completed report is saved as query feedback.

## Files created or modified in this phase

| File | Purpose |
|---|---|
| `app/scheduler/metrics.py` | Bounded collector, injected monotonic timer, queue-age parsing |
| `app/scheduler/runner.py` | Existing worker timing and accepted/deadline origins |
| `app/swing/ledger.py` | Additive job acceptance timestamp and read-only pending summary |
| `app/swing/workspace_api.py` | Optional timing and durable queue metadata |
| `ui/swing.py` | Operations snapshot display and old-response fallback |
| `tests/test_worker_timing.py` | Deterministic synthetic timing, queue and API regressions |
| `tests/test_swing_ui.py` | HTTP metrics display and backward compatibility |
| `tests/test_performance_incremental.py` | Initialize collector in constructor-bypassing test fixtures; retain priority assertions |
| `docs/WORKER_TIMING_CONTRACT.md` | Measurement scope and boundaries |
| `docs/WORKER_TIMING_IMPLEMENTATION.md` | Evidence and handoff |
| `docs/swing/WORKER_TIMING_REAL_CHECK.json` | Read-only local-store Operations observation and hashes |
| `docs/SPEC.md` | Section 37 |
| `README.md` | User-facing Operations timing reference |

Ignored local tooling: `logs/worker_timing_real_probe.py` (reproducible read-only
probe), test logs/basetemps under `logs/worker-timing-*`, Graphify result memory.
All prior uncommitted work retained. No schema, configuration or dependencies
added. Historical evidence/ledger records were not rewritten. Ledger source
changes affect fingerprints of future evaluations, not existing frozen reports.

## Commands and test results

CodeGraph explore calls traced current worker, queue, API/UI and fixture source.
Graphify reflection/lessons and `graphify query` used the existing map. Shell
reads covered `SPEC.md`, applicable skill/query instructions and reports;
`git status --short`, `git diff -- app/scheduler/runner.py`, and `git diff --check`
reviewed scope/whitespace. Source lookup via `rg` was limited to constructor
bypasses after CodeGraph exposed the fixture issue.

The exact offline test commands used `.venv/Scripts/python.exe -m pytest` with
`-q -p no:cacheprovider --tb=short -o faulthandler_timeout=60`, isolated TEMP/TMP
and unique `--basetemp` directories:

1. Focused worker/repair/Operations/ledger/UI run: **1 failed, 89 passed** in
   37.92s. The new failure-injection test patched the scheduler ledger instance,
   while Operations owns another instance. Fixed the test to patch the class.
2. Expanded focused run, adding incremental outcomes: **3 failed, 107 passed**
   in 50.86s. Existing priority tests constructed Scheduler via `__new__` and
   omitted the collector. Initialized that fixture state and retained/asserted
   the scan-priority deferrals. No production fallback was added for incomplete
   object construction.
3. Complete offline suite: **581 passed, 1 warning in 234.10s (0:03:54)**.
4. Final affected-worker/API/UI/incremental-outcomes run: **110 passed, 1 warning**
   in 52.40s. A final metadata-only guard preserves an accepted manual timestamp
   when startup/a scheduler wake observes the same market; its regression passed.
   This targeted run followed that guard; the full run started before the guard.

The final targeted selection was `tests/test_worker_timing.py tests/test_repairs.py
tests/test_swing_operations.py tests/test_swing_ledger.py tests/test_swing_ui.py
tests/test_performance_incremental.py`, with basetemp
`logs/worker-timing-final-boundary/pytest`. The full selection was the entire
suite with basetemp `logs/worker-timing-suite/pytest`. Results are preserved in
each matching directory's `results.txt`; commands printed their actual output.

The 21 new cases cover unavailable/future/naive timestamps; fresh/reset/defensive
snapshots; independently advanced wall and monotonic clocks; live waiting/running
states; cancellation without releasing another owner's lock; exception cleanup;
scan duration including loading/persistence; durable replay age and FAILED/PAUSED
returns; restart recovery and completed/error queue exclusion; absent stores;
manual acceptance/cross-market rejection/startup interference; serial startup
queue delay; outcome and lifecycle failures/durations; stopped-worker API reads,
invalid/unreadable queue metadata; populated HTTP-driven UI and old responses.

`python logs/worker_timing_real_probe.py` (project venv) executed an HTTP GET
through ASGITransport with real configuration/stores, scheduler startup disabled,
replay/outcomes disabled and preview mutations blocked. Actual output:

```text
{"status": 200, "workers": 4, "attempts": 0, "queue": {"status": "absent", "pending": 0, "management_only_pending": 0, "oldest_queued_at": null, "oldest_age_sec": null, "age_status": "empty"}, "protected_files_unchanged": true}
```

The probe stored before/after hashes (including absent stores and WALs), exact
response timing metadata and stopped worker flags. It ran no worker task or
provider download. No socket server/browser was started by that check.

## Assumptions and VERIFIED / NOT VERIFIED

Assume process-local last/max samples plus durable replay age are sufficient for
this measurement phase; selecting percentiles, retention, alerts or scheduling
policy requires later evidence and authorization. The injected Clock is aware;
wall-clock queue age and monotonic elapsed time intentionally have different
meanings. Missing alert timestamps do not justify inventing enqueue ages.

VERIFIED: complete offline suite passed, followed by 110 affected regressions
after the final manual-origin guard. Injected timing boundaries, cancellation,
queue recovery, worker priority, graceful shutdown, older API payloads and
Operations UI rendering passed. The only warning is the existing Starlette
TestClient/AnyIO BlockingPortal deprecation. `git diff --check` passed with only
line-ending conversion notices. The read-only real-store route check returned
HTTP 200, no measured samples, stopped workers and unchanged protected hashes.

NOT VERIFIED: production contention/throughput, long-running worker behavior,
real populated timing UI in a browser and profitability are NOT VERIFIED. The
real-store probe was deliberately idle. Synthetic AppTest checks exercise the
populated HTTP payload display without serving test data through a production
API. No real scan, source import, alert delivery, financial mutation, deployment,
commit or push occurred. Four-hour B1–B6 remain pending.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0, 22 — phase/report discipline | Done | One authorized measurement phase; report, evidence and handoff; stop afterward |
| 2, 3, 5 — integrity/stack/config | Done | Synthetic fixtures isolated; no thresholds/dependencies/config changes |
| 7, 8 — source/provider | Not in this phase | No market download, provider/rule/source changes |
| 15 — scheduler | Done | Existing global lock, priority, schedules, cooldown, pause and shutdown retained |
| 17 — HTTP | Done | Additive optional Operations metrics; queue reads remain off-loop/read-only |
| 18 — UI | Done | HTTP-derived snapshot, observation time, unavailable/old-response handling |
| 20 — tests | Done | 581 full-suite passes; 110 final affected passes; 21 new cases |
| 26–35 — daily research/validation | Done | Existing tests pass; no historical evidence or financial records rewritten |
| 36 — four-hour integration | Not in this phase | B1–B6 remain separately gated; no new hourly worker |
| 37 — worker timing | Done | Queue age, acquisition wait/runtime, deferrals, stages and durable backlog visibility |

## STATE SUMMARY

```text
Completed: measure existing worker waits/runtime; no new background work.
app/scheduler/metrics.py: bounded WorkerTimings collector and queue-age parser.
app/scheduler/runner.py: scan/outcome/replay/lifecycle hooks and queue origins.
app/swing/ledger.py: additive created_at and read-only pending summary.
app/swing/workspace_api.py: optional worker_timings and durable queue health.
ui/swing.py: measured Operations snapshot and older-response fallback.
tests/test_worker_timing.py: injected timing, cancellation, queue/API regressions.
tests/test_swing_ui.py: populated HTTP-metrics display and old-response checks.
tests/test_performance_incremental.py: collector fixture and priority assertions.
docs/WORKER_TIMING_CONTRACT.md: scope and measurement boundaries.
docs/WORKER_TIMING_IMPLEMENTATION.md: implementation/evidence/handoff.
docs/swing/WORKER_TIMING_REAL_CHECK.json: real read-only route/hash observation.
docs/SPEC.md: section 37; README.md: Operations timing reference.
Ignored logs/worker_timing_real_probe.py and worker-timing-* logs: local evidence tools.
Public: WorkerTimings(clock, workers, timer=perf_counter).
measure_lock(worker, lock, *, queued_at=None, queue_source='unavailable'): async context.
deferred(worker), preflight_failed(worker), progress(worker, phase), snapshot()->dict.
queue_age_seconds(created_at: str|datetime|None, now: datetime)->float|None.
PaperLedger.queue_summary()->dict; job(job_id, conn=None) includes existing created_at.
OperationsView.worker_timings: dict defaults empty; scheduler timing_timer optional.
Config keys/dependencies/schema migrations: none.
Decision: busy skips != acquisition wait; unknown age != zero; queue age freezes at start.
Task runtime includes existing off-loop work under the shared lock; no new schedule.
Timings reset per Scheduler instance; durable replay queue ages survive restarts.
No lifecycle enqueue timestamp exists; alert queue age unavailable.
Tests: 581 full passes in234.10s; final affected110 passes in52.40s;1 existing warning.
Real check: HTTP200,4 idle workers,0 attempts,absent replay store,protected hashes equal.
Not verified: production contention/throughput, real populated browser, four-hour strategy.
Prior edits preserved; no real scans/imports/alerts/account mutations/commit/deployment.
Next: await CONTINUE; reread docs/SPEC.md and review observed timing samples before more work.
```

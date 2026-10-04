# Signal outcome optimization — completed 2026-10-04

Integrated the user's rebuilt patch, preserving resolved outcome attribution. Signal ingestion now uses an indexed cursor and bounded, atomic transactions. Unchanged reports avoid the SQLite writer slot. Schema v2 tracks the cursor and initialized evaluation contexts; existing databases migrate automatically. SQLite uses WAL and a configurable busy timeout.

Two reproduced defects in the supplied rebuild were corrected: returning to a previous benchmark omitted newer cohorts, and a manual reservation, active scan, or shutdown arriving during the asynchronous due-check could lose priority. New cohorts now populate every registered context, and the scheduler rechecks scan priority after preflight. Context registration and cursor advancement serialize with competing writers. Healthy batches continue on the next tick; failed batches retain backoff.

## Files created / modified

- Created: `tests/test_performance_incremental.py`, this report.
- Modified: `app/performance/service.py`, `app/performance/repository.py`, `app/cache/persistence.py`, `app/core/config.py`, `app/scheduler/runner.py`, `tests/test_performance.py`, `.env.example`, `.gitignore`.
- Local runtime: backed up `data/history.db` to ignored `logs/history-before-outcome-v2.db`; upgraded the real database; reloaded the existing API preview with its scheduler-disabled configuration preserved.

## Commands executed and actual results

```powershell
$taskTmp = Join-Path (Get-Location).Path ('logs/outcome-tests-' + [guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp $taskTmp
.venv/Scripts/python.exe -m compileall -q app tests/test_performance_incremental.py
git diff --check
```

Final pytest output: **148 passed, 1 warning in 7.39s**. The warning concerns Starlette's deprecated AnyIO alias. Compile and whitespace checks exited successfully; Git additionally reported existing LF/CRLF normalization notices.

The initial supplied rebuild passed 140 tests. Added regressions then reproduced four failures: benchmark round-trip plus reservation, active-scan, and shutdown races. Final tests also cover chunk rollback/resumption, parallel context initialization, and unchanged report / due-check reads while another connection holds the writer slot. All test market data is SYNTHETIC and isolated from the running API.

Additional commands used: `git status --short`, `git diff --stat`, focused pytest, CodeGraph source exploration, PowerShell process identification/restart, and Python SQLite/HTTP probes. The HTTP probes used `urllib.request.urlopen` against `/api/status` and `/api/performance?market=NSE&horizon=5`; both returned **200**. SQLite `PRAGMA integrity_check` returned **ok** and `PRAGMA journal_mode` returned **wal**.

Real database before / after: NSE **54 / 54** signals, NYSE **39 / 39** signals, **18** cohorts and **54** horizon outcomes. Migration versions changed from `[1]` to `[1, 2]`. The five-session report has 18 pending, 0 matured, 0 resolved; hit rate remains null.

## Assumptions and limits

- This is one optimization phase of outcome tracking. NSE delivery percentage, relative strength, and sector enrichment remain queued.
- Completed outcome attribution freezes once any horizon resolves. Earlier captures arriving before resolution may still correct the canonical entry schedule.
- Reports ingest newly captured signals and may write when data or an evaluation context changes. Report aggregation/export still scale with the selected cohort count; this patch removes repeated historical ingestion, not all historical report work.
- Configuration defaults: `PERFORMANCE_SYNC_CHUNK=5000`, `SQLITE_BUSY_TIMEOUT_SEC=30`; both are environment-configurable and validated.
- VERIFIED: regression suite, concurrent SQLite behavior, migration on the existing database, local API responses, preserved raw history.
- NOT VERIFIED: live yfinance outcome downloads, the attachment's 156,000-row throughput timings, and unattended nightly execution. The preview retains its previously disabled scheduler. No synthetic outcomes were served by the API.

## SPEC COMPLIANCE

| Constraint | Evidence | Status |
|---|---|---|
| One phase; read SPEC first | Outcome optimization only; `docs/SPEC.md` read | YES |
| No fabricated production market data | Synthetic fixtures only; real counts preserved | YES |
| Configurable operational settings | Validated chunk size / timeout and `.env.example` | YES |
| Non-blocking API / injected clock | Existing worker execution retained; async due preflight | YES |
| Single-flight scan priority | Reservation / lock / shutdown regressions pass | YES |
| Local persistence and provenance | Versioned atomic migration, WAL, frozen resolved cohorts | YES |
| Report remains HTTP-only in UI | No UI data-access changes | YES |
| Tests executed; limits disclosed | 148 passed; live / large-scale claims unverified | YES |

## STATE SUMMARY

```text
Phase: signal outcome optimization complete.
User rebuild integrated, with benchmark round-trip and scheduler-race fixes.
Schema: v1 + v2; indexed incremental cursor; bounded atomic chunks.
Resolved history remains frozen; raw scans/signals retained.
SQLite: WAL; configurable timeout; backup in logs.
Checks: 148 passed; compile and diff checks successful.
Local API: both probes 200; database integrity ok.
Real signals: NSE 54, NYSE 39; cohorts 18; outcomes 54 pending.
Preview scheduler remains disabled, matching its prior configuration.
Live provider and attachment's throughput figures not verified.
Next phase: NSE context layer; await CONTINUE.
```

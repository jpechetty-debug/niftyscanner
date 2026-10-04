# Review of pasted outcome-tracking optimization

Review only. The pasted patch was reconstructed in an isolated in-memory module;
no production source, scheduler settings or real history was changed.

## Verdict

The diagnosis is substantially correct. Current `sync()` processes all NSE signal
rows on every report and before the nightly job's due/completion check. The existing
scheduler holds the global scan lock around that call. The 300-second retry delay
also applies to healthy continuing batches. Incremental ingestion and separating
healthy continuation from failure backoff are appropriate fixes.

Do not apply the pasted diff unchanged:

1. **High priority: changing a cohort can invalidate resolved outcomes.** The patch
   updates its canonical signal, entry date and score, but updates exit dates only
   for pending/unresolved outcomes. A later-inserted earlier capture therefore
   leaves resolved prices/returns associated with the old entry. The joined report
   can attribute that old performance to the new score bucket.
   Reproduction: resolve a capture entering Oct 6 with score 0.8; then ingest an
   earlier capture entering Oct 5 with score 0.2. The patched report shows entry
   Oct 5, one-session exit Oct 6, status resolved and the old excess return.
   A one-session trade must enter and exit on the same session. Correct handling
   needs an immutable cohort revision (preferred given outcome immutability), or
   explicitly invalidated/re-evaluated outcomes with preserved revision history.
   The current implementation also fails to promote an earlier capture arriving
   after its original sync; existing tests cover ordering within an initial sync,
   rather than this resolved-history case.

2. **Medium priority: the replacement sync still scales with all outcomes.** Its
   `INSERT ... SELECT ... GROUP BY cohort_id,horizon` runs unconditionally, even
   when no new signals or evaluation context exist. SQLite's query plan confirms
   `SCAN signal_outcomes USING INDEX sqlite_autoindex_signal_outcomes_1`.
   Initialize a new benchmark/basis/evaluation context once, from the canonical
   cohort's entry schedule; subsequently create outcomes only for newly ingested
   cohorts. Copying `MAX(exit_session)` from old contexts is unsafe if schedules
   differ after a cohort correction. Put sync/context tracking in a versioned
   migration and commit cursor advancement together with its data writes.

3. **Locking is improved, but not eliminated.** GET still writes, the due guard
   still follows sync, and first backfill still holds a write transaction for its
   work. Check whether nightly work is due before taking the shared scan slot,
   process backfill in bounded transactions, and move ingestion off the report
   path when making GET read-only. A reported 14-second backfill is longer than
   the connection's default five-second SQLite busy timeout, so the 2.6-second
   concurrent-save measurement does not establish safety at backfill scale.

The healthy-batch retry change is correct: keep failure backoff for `failed`, and
let `continuing` resume at a subsequent scheduler tick, retaining the scan lock,
market gating and manual reservation checks.

## Verification performed

Commands executed:

```powershell
.venv/Scripts/python.exe -m logs.review_signal_patch --probe
.venv/Scripts/python.exe -m logs.review_signal_patch --suite
```

Actual suite result against the reconstructed scratch patch:
`132 passed, 1 warning in 5.85s`.
The warning is the existing Starlette/AnyIO BlockingPortal deprecation.

Actual isolated diagnostic result:

```text
Before: signal 1, score 0.8, entry 2026-10-06, exit 2026-10-06, resolved, excess ≈8 pp.
After:  signal 2, score 0.2, entry 2026-10-05, exit 2026-10-06, resolved, same excess.
Context-copy query plan: SCAN signal_outcomes USING INDEX sqlite_autoindex_signal_outcomes_1.
Context-copy statement executed again with no new signals: True.
```

Read-only real database inspection: NSE 54 signals, NYSE 39 signals; 18 NSE
cohorts. The stated 8,000 signals/day is a workload estimate, not an observed
daily count in this database. Forty cohorts at five per batch require eight
batches: seven five-minute waits explain 35 minutes of idle time. Seven 15-second
ticks explain 1m45s of idle time; real downloads and processing add to both.

VERIFIED: source diagnosis, scratch suite, resolved-cohort mismatch and recurring
full-outcome scan. NOT VERIFIED: the supplied 14s/2.6s measurements, 8,000/day
production throughput, real-provider backlog completion time or live stock data.

## Files and assumptions

Created `logs/review_signal_patch.py` (ignored, SYNTHETIC scratch diagnostic) and
`docs/SIGNAL_OUTCOME_PATCH_REVIEW.md` (this report). Application files unchanged.
Assumptions: supplied fragment accurately represents the proposed diff; signal
history is append-only; benchmark changes must preserve existing evaluated history.
Do not prune original partial rows as part of this patch without an explicit
archive/retention design that preserves cohort references and audit provenance.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0 / phase discipline | Done | One requested review; no implementation phase started. |
| 2 / data integrity | Done | SYNTHETIC scratch only; real database inspected read-only. |
| 14 / persistence | Done | Cursor, provenance and lock risks reviewed; no production changes. |
| 15 / scheduling | Done | Healthy continuation and global-lock behavior checked. |
| 20 / tests | Done | Actual executed suite and diagnostic results shown. |

## STATE SUMMARY

```text
Phase complete: review of pasted signal-outcome optimization.
Created docs/SIGNAL_OUTCOME_PATCH_REVIEW.md: findings, verification and next steps.
Created ignored logs/review_signal_patch.py: reconstructed in-memory patch and SYNTHETIC probes.
Production code/database/config changes: none.
Public production classes/functions/config keys changed: none.
Scratch tests: 132 passed, one existing dependency warning, 5.85s.
Extra diagnostic reproduced resolved-cohort entry/exit inconsistency.
SQL plan confirmed full outcome scan; no-new-signal sync still executes it.
Existing full-history sync and healthy-batch failure delay are real inefficiencies.
Recommendation: incremental cursor with versioned migration; bounded backfill.
Recommendation: immutable cohort revisions; context initialization once per context.
Recommendation: healthy continuation at scheduler ticks; failures retain backoff.
Known limits: supplied timings and live yfinance throughput not independently verified.
Next phase first step: on implementation authorization, reread SPEC and add failing
regressions for late earlier captures after resolution and unchanged-context sync.
```

# Signal outcome tracking: reviewed implementation scope

Date: 4 October 2026. This replaces ranking explanations/comparison as the first
feature priority. Application code and the production database are unchanged.
Implementation awaits the repository's explicit CONTINUE requirement.

The user's second feature stage is the NSE delivery/relative-strength/sector
context layer, specified in `docs/NSE_CONTEXT_PLAN.md`. Build outcome tracking
first; add contextual measurements next; change scoring only after evaluation.

## Audit evidence

Read-only inspection of `data/history.db` found:

| Market | Stored scans | Signal rows | Distinct tickers | Recorded bar dates |
|---|---:|---:|---:|---|
| NSE | 3 | 54 | 18 | 1 October 2026 only |
| NYSE | 1 | 39 | 39 | 2 October 2026 only |

All stored rows have `session_partial=0`. Each of the 18 NSE tickers has three
records for the same bar date. The table has signal/scan identifiers, stock
metrics, score, market, partial flag and bar date; no outcome or strategy-context
columns. Scan timestamps are available by joining `scans`.

These 54 NSE rows are not 54 independent daily picks. Initial results cannot
support a meaningful hit-rate estimate, and future horizons must mature first.
Historical scoring configuration was not stored and must remain unknown.

## Measurement contract

- First release: NSE only, against the actual Nifty 500 index. NYSE remains
  usable in the screener; Performance explains that no NYSE benchmark is enabled.
- Retain every original signal. Create a separate evaluation cohort with one
  completed-bar signal per market/ticker/bar date/known strategy version. Use the
  earliest eligible recorded signal, not the later record that looks best after
  its outcome. Unknown legacy strategy versions form an explicit legacy cohort.
- Exclude partial bars from the initial daily-cohort report, count the exclusion,
  and avoid retrospectively relabeling their intraday score as a closing score.
- Proposed primary timing: enter at the next tradable session's open after the
  signal became available; measure exits at the close of the 1st, 5th and 10th
  holding sessions. Store entry/exit dates and the original bar date separately.
  This avoids assuming a purchase at a closing price observed only after close.
- A bar-close-to-forward-close research metric may be added later as a separately
  labeled basis. It must not be mixed into the executable-timing series.
- Count exchange trading sessions, not calendar days or the next available
  ticker row. A missing exit quote on the required date is unresolved, not a
  reason to move the exit forward. Use the existing injected Clock and calendar;
  keep the disclosed XBOM proxy limitation and detect benchmark/date mismatches.
- For horizon h: stock return = exit / entry - 1; benchmark return uses the same
  entry-open and exit-close dates; excess return = stock return - benchmark
  return, reported in percentage points. A hit means excess return > 0; zero is
  a non-hit. Gross returns exclude execution costs and slippage and are labeled.

## Price and benchmark basis

The initial proposed basis is split-adjusted price return for both stocks and
the Nifty 500 price index, excluding ordinary cash dividends from both sides.
The existing screener's `auto_adjust=True` calculation remains unchanged.
Outcome history needs a separate explicit price-return basis, corporate-action
checks, and common adjustment vintage for entry and exit.

Yahoo's own index lookup identifies `^CRSLDX` as NIFTY 500. Live historical-bar
availability and adjustment behavior are NOT VERIFIED by this audit. Before
enabling the job, verify the installed provider's index identity, usable daily
OHLC dates, stock split handling and price-return behavior. Never replace the
benchmark silently with Nifty 50, an ETF, or an average of today's constituents.
If that contract cannot be verified, leave evaluation unavailable and report it.

NSE Indices distinguishes price and total-return indices: the latter includes
dividend receipts. Dividend-adjusted stock returns must not be compared to a
price-only index. A future total-return mode would require a verified Nifty 500
TRI source and explicit approval to extend the current yfinance-only contract.

Do not divide a newly adjusted future quote by the old stored `signals.price`:
download/cache entry and exit together on a consistent basis. Preserve original
signal metrics and store evaluation prices, basis, source, retrieval timestamp
and revision separately. Validate corporate-action discontinuities and keep
unresolved data visible; never fabricate a delisting payout or a terminal loss.

Primary references consulted:

- [Yahoo index lookup](https://es-us.finanzas.yahoo.com/lookup/index/?s=500)
- [yfinance download contract](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)
- [NSE Indices total-return explanation](https://www.niftyindices.com/resources/index-concepts/total-return-index)
- [NSE historical index reports](https://www.niftyindices.com/reports/historical-data)

## Persistence and nightly processing

Add transactional, versioned migrations without deleting existing scan history:

- `signal_cohorts`: canonical signal reference, availability timestamp, entry
  session, original metrics, strategy-context/hash, return basis and exclusion.
- `signal_outcomes`: cohort/horizon unique key; required exit session; stock and
  benchmark entry/exit values; returns; pending/resolved/unresolved status;
  reason, source, timestamps and evaluation version.
- `performance_prices`: reusable dated OHLC series keyed by instrument, basis
  and adjustment vintage; validated provider provenance.
- `performance_jobs`: durable last attempt/success, due date, progress, retries
  and error summary, supporting restart catch-up and idempotent reruns.

Job default proposed for implementation: 21:00 Asia/Kolkata, configurable through
environment/.env. It belongs in the application's existing scheduler, not a
Codex reminder or a second external scheduling service. Catch up overdue work
on startup, fetch only due cohorts and reusable missing history, prioritize
screening work, and honor provider rate limits/backoff. Provider and SQLite work
run outside the FastAPI event loop. Short DB transactions, one evaluation run at
a time, atomic outcome upserts and bounded retries prevent overlapping writes.

Proposed config additions: enable flag, nightly local time, benchmark ticker,
return basis, score/RSI/volume bucket edges, minimum report sample size and job
retry/batch limits. Validate finite ordered bucket edges and time/count values.
Default horizons are the user-requested 1, 5 and 10 sessions; capture their
definition and all active strategy settings with future cohorts.

## API and Performance tab

Add HTTP-only read endpoints for performance aggregates and outcome rows. Keep
existing results/refresh/settings endpoints compatible. The UI adds a compact
Performance tab with horizon, signal-date range and strategy-cohort filters.

For each score bucket, volume-ratio bucket and RSI band, show:

- Eligible, matured, pending, unresolved and excluded counts.
- Valid-pair sample count and coverage; hit rate, mean and median excess return.
- Mean stock and benchmark returns, benchmark identity and return/timing basis.
- Sample-size cautions; low/high hit-rate bounds treating unresolved matured
  cases as all losses/all wins, without pretending their returns are known.
- Last job success, errors and an exportable signal-level audit table.

Use fixed configurable bucket edges, including explicit boundaries and empty
buckets; do not move boundaries until results look favorable. Compare horizons
over equivalent date/strategy cohorts and disclose overlapping picks across
sessions. Repeated tickers/dates are correlated observations, not independent
evidence. No automatic threshold optimization or significance claims.

## What the history can and cannot answer

It can measure recorded qualified picks and stricter subsets, subject to missing
data and coverage. It cannot evaluate stocks rejected by the original screening
thresholds, reconstruct past point-in-time index membership, or show the effect
of lowering those thresholds. Record feature/decision snapshots for the full
universe in a later phase before calling this general threshold backtesting.

Always evaluate historical recorded tickers, even if absent from today's
universe. Preserve unresolved/delisted cases and show coverage. This reduces
silent survivor exclusion but does not eliminate provider survivorship bias.
Separate legacy/unknown strategy history from newly captured known strategies.

## Acceptance tests and planned files

Tests use explicitly labeled synthetic inputs only: exact independent return
calculations; holidays/weekends; delayed and after-close signals; missing date
quotes; splits/dividends and common price vintage; benchmark absence; partial
exclusion; duplicate scans; zero/tied returns; bucket boundaries; idempotence;
transaction rollback; restart catch-up; rate-limit retries; API responsiveness;
unknown legacy settings; low/zero sample states; UI filter/export consistency.

Planned files: focused outcome models/repository/service/provider modules under
`app/`; scheduler and lifespan wiring; API models/routes; config and `.env.example`;
`ui/app.py`/styles; tests and synthetic fixtures; SPEC/README documentation.
No dependencies, production data or application code changed by this audit.

## Commands and verification

Reread `docs/SPEC.md`; explore persistence, scheduler and provider through
CodeGraph; query `data/history.db` via SQLite `mode=ro`; browse the primary
references above; execute:

```powershell
$taskOutcomeAuditTemp = Join-Path (Get-Location).Path ('logs/outcome-audit-tests-' + [guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp $taskOutcomeAuditTemp
git diff --check
git status --short
```

Real output:

```text
........................................................................ [ 64%]
.......................................                                  [100%]
111 passed, 1 warning in 5.03s
```

The warning is the existing Starlette/AnyIO BlockingPortal deprecation.
VERIFIED: stored-history schema/counts, existing implementation and baseline
tests. NOT VERIFIED: new job/API/UI, live benchmark OHLC coverage and consistent
price-return handling. Assumptions: NSE first, fixed 1/5/10 holding-session
horizons, proposed next-open entry, price-return basis, no threshold tuning yet.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0 — Phase discipline | Done | Reviewable replacement scope; await CONTINUE. |
| 2 — Data integrity | Done | Read-only production DB inspection; no invented outcomes. |
| 3, 7, 8 — Data source/provider/hygiene | Not in this phase | Same provider proposed; outcome price basis must be verified. |
| 5, 9, 12 — Config/partial/ranking | Not in this phase | Existing behavior preserved; future context capture scoped. |
| 14, 15 — Persistence/scheduler | Not in this phase | Non-destructive migrations and integrated nightly job planned. |
| 16, 17, 18 — Market/API/UI | Not in this phase | Session-aligned benchmarks and Performance tab scoped. |
| 20 — Tests | Done | Existing full suite executed; 111 passed. |
| 22 — Reporting | Done | Commands, evidence, limitations and state recorded. |

## STATE SUMMARY

```text
Completed: signal-outcome feature audit and reviewable implementation scope.
Created docs/SIGNAL_OUTCOME_PLAN.md: methodology, persistence, API/UI and tests.
Modified docs/FEATURE_IMPROVEMENT_PLAN.md: earlier features explicitly deferred.
Application/database changes: none.
Public functions/classes added or changed: none.
Config keys added: none; proposed keys documented above.
History: NSE 54 rows = 18 repeated ticker/bar-date cohorts; NYSE 39 rows.
Decision proposed: next-open entry, 1/5/10 holding-session close exits.
Decision proposed: split-adjusted price returns against Nifty 500 price index.
Decision: unresolved cases and historical tickers remain visible.
Tests: 111 passed, one existing dependency warning.
Known limits: provider benchmark/price basis needs verification; no outcomes built.
Next phase first step: after CONTINUE, reread SPEC and verify provider price basis
before implementing versioned outcome persistence and independent calculations.
Stopped: awaiting explicit CONTINUE required by AGENTS.md and SPEC section 0.
```
# Implementation update

This reviewed scope is implemented in [the phase report](SIGNAL_OUTCOME_IMPLEMENTATION.md).
The earlier audit/state summary below is historical; the implementation report has current status.


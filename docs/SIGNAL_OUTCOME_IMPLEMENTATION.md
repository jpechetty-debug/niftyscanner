# Signal outcome tracking — implementation phase

Implemented the first feature from the reviewed plan. The user's explicit
"implement the plan using less tokens" authorizes this implementation phase.
The NSE context layer remains a separate next phase.

## Behavior

- Completed daily signals become one earliest-capture cohort per ticker, bar date,
  and strategy settings. Legacy settings stay unknown. Partial captures are excluded.
- Entry is the first session open after both capture and the signal bar's close.
  Exits are the closes of holding sessions 1, 5 and 10. Holidays use the existing
  XBOM calendar proxy; quotes never substitute a later available date.
- Stock and Nifty 500 price returns use a matching entry/exit window fetched in
  one vintage. Excess is stock return minus index return, in percentage points.
  A hit requires strictly positive excess; ties are misses. Fees, slippage and
  ordinary cash dividends are excluded. Split-adjusted Yahoo OHLC is used; the
  old saved screener price is never used as the entry price.
- SQLite stores cohorts, outcomes, price provenance and durable job status in a
  transactional, versioned extension. Original history is retained. Different
  benchmark/basis/evaluation contexts cannot mix resolved outcomes.
- The existing scheduler evaluates at 21:00 IST, catches up missed runs, processes
  bounded batches in worker threads under the shared scan lock, and retries
  systemic failures within a bounded budget. Individual missing/delisted names
  remain unresolved without preventing other signals from being evaluated.
- GET `/api/performance` supports market, 1/5/10-session horizon, signal-date range
  and strategy filters. Reporting performs no price downloads. The Performance
  tab adds score/volume/RSI buckets, coverage, small-sample flags, pending/missing
  counts, descriptive means/median and a complete outcome CSV audit export.

## Running

Use the regular backend (not the read-only UI preview):

```powershell
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Stop the current preview listener before starting it on that port. The regular
backend performs the existing mandatory startup scans and then nightly evaluation.
The computer/backend must remain running; this is not an OS or Codex automation.
Configuration keys and defaults are in `.env.example`; no new dependencies.

The current preview deliberately retains `create_app(start_scheduler=False)`;
the API and UI explicitly report that nightly jobs are paused there.

## Files created / modified

Created: `app/performance/{__init__,repository,provider,service,api}.py`,
`ui/performance.py`, `tests/test_performance.py`, this report.
Modified: `.env.example`, `.gitignore`, `app/core/config.py`,
`app/cache/persistence.py`, `app/services/state.py`, `app/main.py`,
`app/scheduler/runner.py`, `ui/api_client.py`, `ui/app.py`, `tests/test_ui.py`,
`docs/FEATURE_IMPROVEMENT_PLAN.md`, `docs/SIGNAL_OUTCOME_PLAN.md`.
Runtime artifacts: migrated `data/history.db`, ignored `data/yfinance-cache/`,
`logs/preview_performance_api.py`, server logs and `logs/performance-preview.png`.

## Commands executed and real results

- CodeGraph explored the modified classes/functions and their callers.
- `.venv/Scripts/python.exe -m compileall -q app/performance ui` — exit 0.
- Full pytest command (fresh workspace temporary directory each run):

```powershell
$taskTmp = Join-Path (Get-Location).Path ('logs/outcome-tests-' + [guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp $taskTmp
```

Actual final suite output: `132 passed, 1 warning in 6.23s`.
After the final table-format-only change, the performance UI control/export test
was rerun: `1 passed, 1 warning in 2.52s`. The warning is the existing
Starlette/AnyIO BlockingPortal deprecation.

- `git diff --check` — exit 0; Git emits existing CRLF-conversion notices.
- Live `YahooOutcomeProvider.history('^CRSLDX', 2026-09-21, 2026-10-01)`:
  9 valid sessions; last session 2026-10-01, Open 22005.0, Close 21857.75.
- `Invoke-RestMethod http://127.0.0.1:8000/api/performance?horizon=5`:
  supported=true, scheduler_running=false, eligible=18, pending=18,
  matured=0, evaluated=0, hit rate=null. No invented outcomes.
- Read-only SQLite audit after migration: NSE signals=54, NYSE signals=39;
  NSE cohorts=18, outcomes=54 pending across three horizons. 36 repeat captures
  removed from analysis, with all original rows retained.
- Reloaded the identified API preview using the same disabled-scheduler profile.
  Windows `Stop-Process` failed; verified listener was restarted successfully via
  Windows process APIs. Browser verified the live tab, grouped RSI counts,
  empty-outcome state and paused-job notice; screenshot saved locally.

## Assumptions and limits

- NSE only initially; NYSE receives an explicit unsupported-performance response.
- Existing XBOM session proxy is used, not a newly implemented NSE holiday service.
- Yahoo daily OHLC is treated as split-adjusted price data. Its separate
  dividend-adjusted series is not used. Same-window vintage storage prevents
  accidental mixing, but cannot guarantee against Yahoo's source errors.
- Legacy scans cannot reconstruct their original settings or rejected candidates.
  Current qualified history can evaluate existing picks and stricter subsets,
  not the results of loosening past thresholds. Same-day signals are correlated.
- Missing/delisted prices remain unresolved; no assumed delisting payout.
- Resolved outcomes are immutable for their evaluation context. Historical price
  revisions require a new evaluation version rather than silent replacement.

VERIFIED: independent synthetic return arithmetic, session counting, holidays,
capture-time entry, split/dividend basis selection, missing exact quotes, benchmark
failure, duplicate/partial handling, bucket boundaries/ties, filter validation,
transaction rollback, retries, catch-up, batching, idempotence, non-blocking workers,
UI controls/export, live benchmark retrieval, API and retained real history.

NOT VERIFIED: actual forward returns for the current cohort (sessions are still
in the future), a wall-clock unattended 21:00 production run, live corporate-action
accuracy for every historical stock, official NSE holiday differences from XBOM.

## SPEC COMPLIANCE

| Sections touched | Status | Note |
|---|---|---|
| 0 / phase discipline | Done | One authorized implementation phase; next layer deferred. |
| Configuration / validation | Done | Environment-driven settings, validated defaults. |
| Providers / Clock / calendar | Done | Separate outcome protocol; injected Clock; existing proxy disclosed. |
| Data integrity / failure handling | Done | No fabricated production data; missing results remain visible. |
| Persistence | Done | Versioned transactional extension preserves original rows. |
| Scheduling / concurrency | Done | Existing scheduler, worker threads, shared lock and bounded retries. |
| API / UI separation | Done | HTTP-only UI; additive validated reporting endpoint. |
| Tests / verification | Done | Real executed results and live limits recorded above. |
| NSE delivery / RS / sector | Not in this phase | Queued after outcome tracking. |

## STATE SUMMARY

```text
Phase complete: signal outcome tracking implementation.
app/performance/repository.py: migrate(conn), strategy_context(config), PerformanceRepository(data_dir).
app/performance/provider.py: OutcomePriceProvider.history(ticker,start,end), YahooOutcomeProvider(timeout,cache_dir).
app/performance/service.py: PerformanceService(config,clock,calendar,provider,repository=None).
Service methods: sync(conn), run_if_due(), report(market,horizon,start,end,strategy), summarize(rows).
Pure function: returns(stock_entry,stock_exit,benchmark_entry,benchmark_exit).
app/performance/api.py: validated GET /api/performance and PerformanceResponse.
app/performance/__init__.py: package boundary.
app/core/config.py + .env.example: PERFORMANCE_* validated configuration.
app/cache/persistence.py: migration and scan strategy persistence.
app/services/state.py: future scan strategy snapshot.
app/main.py: provider/service/router integration.
app/scheduler/runner.py: evaluate_performance_if_idle(), is_running property.
ui/api_client.py: get_performance(...), HTTP only.
ui/performance.py: render_performance(client,market), buckets/filtering/audit CSV.
ui/app.py: Performance tab.
tests/test_performance.py: SYNTHETIC outcome, scheduler, API and UI checks.
tests/test_ui.py: isolate existing UI tests from live performance HTTP.
.gitignore: ignore local Yahoo cookie/timezone cache.
docs/SIGNAL_OUTCOME_IMPLEMENTATION.md: phase report and verification.
docs/FEATURE_IMPROVEMENT_PLAN.md + docs/SIGNAL_OUTCOME_PLAN.md: completion pointers.
docs/NSE_CONTEXT_PLAN.md: retained next-phase design, no implementation.
Config keys: ENABLED, DATA_DIR, NIGHTLY_TIME, BENCHMARK, PRICE_BASIS, BATCH_SIZE,
RETRY_SEC, MAX_RETRIES, TIMEOUT_SEC, MIN_SAMPLE, SCORE_EDGES, VOLUME_EDGES, RSI_EDGES
(all prefixed PERFORMANCE_).
Database extension version 1; old JSON snapshot version remains 1.
Real history retained: NSE54/NYSE39 signals; 18 NSE daily cohorts, 54 pending outcomes.
Tests: 132 passed; final formatting UI recheck 1 passed; one existing dependency warning.
Preview at 127.0.0.1:8501; API8000 intentionally has scheduler disabled.
Nightly evaluation enabled by default in regular backend; must stay running.
Known limits: XBOM proxy, Yahoo source quality, future current-cohort exits, no NYSE benchmark.
Next phase first step: after explicit CONTINUE, reread SPEC and docs/NSE_CONTEXT_PLAN.md.
Implement dated delivery, relative strength and industry context without changing scores first.
```

Provider references: [Yahoo Nifty 500 symbol listing](https://es-us.finanzas.yahoo.com/lookup/index/?s=500),
[yfinance source](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py),
[maintainer price-adjustment discussion](https://github.com/ranaroussi/yfinance/discussions/1682).

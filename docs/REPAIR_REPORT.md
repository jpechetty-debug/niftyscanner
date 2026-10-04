# CodeGraph repair phase — 2026-10-04

Fixed scheduler crashes, completion-based timing, cross-market single flight, manual-refresh reservations, shutdown draining, bounded P/E dispatch/retries, single half-open trials, scan-failure propagation, retry request accounting, NYSE date normalization, stable refresh deadlines and independent data timestamps. Added atomic versioned JSON snapshots alongside existing SQLite history and offloaded universe loading/persistence. The UI renders the countdown and treats search as literal text. Invalid operational settings fail fast; failed settings writes preserve runtime values.

Assumption: retain the strategy introduced by recent commits (RSI trend, projected volume, finite non-positive P/E and the piecewise P/E score). README, SPEC and AGENTS now describe that behavior; OPTIMAL_PE is configurable. The existing light theme and XBOM NSE proxy are disclosed. No live data was fabricated or fetched.

## Files

The STATE SUMMARY below lists all created/modified project files. The ignored .venv contains the pinned project dependencies; global Python packages were not changed. Temporary test directories were removed. No commit or deployment was made.

## Commands and executed results

- CodeGraph codegraph_explore for affected symbols and call paths; Get-Content docs/SPEC.md and affected configuration/source; git status/diff/diff --check.
- python -m pytest -q -m 'not live' -p no:cacheprovider --basetemp=.repair-test-1 --tb=short: 72 passed, 2 failed; corrected validation order and replaced obsolete per-symbol chunk expectations.
- Same command with .repair-test-2: 93 passed.
- python -m venv .venv.
- .venv/Scripts/python.exe -m pip install --disable-pip-version-check -r requirements.txt: initial sandbox socket restriction; approved escalation succeeded.
- .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.repair-test-3 --tb=short: 93 passed.
- Subsequent runs after additional fixes/checks, using .repair-test-4 / 5: 96 / 97 passed.
- Final: .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.repair-test-6 --tb=short.
- .venv/Scripts/python.exe -m pip check.
- git diff --check: exit 0; only Git LF/CRLF notices.

Final real output:

```text
98 passed, 1 warning in 3.31s
No broken requirements found.
```

The warning is Starlette TestClient's deprecated AnyIO BlockingPortal alias. Tests exclude live checks by default, use mocked providers/SYNTHETIC fixtures, and cover the actual scheduler loop, real worker-thread HTTP responsiveness, shutdown, breaker trips/recovery, retries, snapshots, settings failure and Streamlit countdown rendering.

VERIFIED: final offline suite, Streamlit AppTest render, isolated dependency consistency, clean diff whitespace.
NOT VERIFIED: live Yahoo data, live market-hour accuracy against NSE (XBOM remains a proxy), production performance or browser pixel layout.
Blockers: none for this repair phase.

## SPEC COMPLIANCE

Statuses apply to this phase's touched requirements, not an exhaustive whole-project certification.

| Section | Status | Note |
|---|---|---|
| 0 Operating rules | Done | One repair phase; spec re-read, results recorded. |
| 1 Goal | Done | Documents existing strategy rather than stale defaults. |
| 2 Development contract | Done | Mocked/SYNTHETIC tests; data limitations preserved. |
| 3 Stack | Done | Isolated pinned environment; existing calendar proxy documented. |
| 4 Structure | Done | Only repair/configuration/report files added. |
| 5 Configuration | Done | Validation, settings precedence, OPTIMAL_PE and env example aligned. |
| 8 Hygiene | Done | Naive exchange-date preservation and safe adjustment refetches. |
| 9 Indicators | Done | Existing projection documented with its limits. |
| 10 Pipeline | Done | P/E input semantics reconciled; survivor-only fetching retained. |
| 11 Outcomes | Done | Wildcard systemic records; affected counts tracked separately. |
| 12 Ranking | Done | Existing piecewise score documented; peak configurable. |
| 13 Resilience | Done | Bounded dispatch/retries, half-open trial and explicit abort. |
| 14 Persistence | Done | Atomic versioned snapshots plus legacy SQLite restore/history. |
| 15 Refresh | Done | Global slot, completion deadlines, one post-close attempt, drain on stop. |
| 16 Status | Done | Time/calendar behavior retained; NSE proxy limitation disclosed. |
| 17 API | Done | Stable deadline, actual bar timestamp, 429 retry_after, safe settings writes. |
| 18 UI | Done | Countdown rendered; HTTP boundary retained. |
| 20 Tests | Done | 98 tests passed; misleading sleep-based test replaced. |

## STATE SUMMARY

```text
Phase completed: CodeGraph repair (one phase).
.env.example: expose RSI trend and configurable P/E peak.
AGENTS.md: describe partial-session projection accurately.
README.md: current strategy, timestamps, proxy, isolated setup.
app/api/models.py: add next_refresh_at to status response.
app/api/routes.py: retry metadata; offload safe settings writes.
app/cache/persistence.py: atomic snapshots plus SQLite fallback/history.
app/core/config.py: validate operations and P/E peak; safe overrides.
app/core/indicators.py: preserve naive exchange-local dates.
app/core/outcomes.py: internal affected counts for wildcard failures.
app/core/ranking.py: configurable P/E peak.
app/providers/yfinance_provider.py: bounded retries, breakers, counters.
app/scheduler/runner.py: global reservations, completion timing, drain.
app/services/scanner.py: explicit abort and independent data timestamp.
app/services/state.py: shared lock, stable deadlines, snapshot state.
docs/SPEC.md: reconcile current strategy and repair contracts.
docs/REPAIR_REPORT.md: phase evidence and handoff.
tests/test_api.py: remove misleading simulated responsiveness test.
tests/test_regressions.py: verify wildcard chunk accounting.
tests/test_repairs.py: meaningful offline runtime/UI regression checks.
ui/app.py: live countdown and literal text search.
pytest.ini: exclude live tests by default.
.venv/ (ignored): isolated pinned project runtime.
Public: Scheduler.wake() -> None; execute_scan(market='NSE') -> bool unchanged.
Public: ScanStateManager.get_next_refresh_at(market: str) -> Optional[datetime].
Public: ScanStateManager.persist_last_scan(market: str) -> None.
Public: update_scan_success adds data_as_of: str='', persist: bool=True.
Public: FunnelTracker.record_failure adds affected_count: int=1.
Provider protocol and six-element run_scan return tuple unchanged.
Config added: OPTIMAL_PE=10; REQUIRE_RSI_TREND_UP already existed.
Decision: preserve recent strategy and existing SQLite history/theme.
Known issues: upstream deprecation warning; live behavior unverified.
Next step: re-read docs/SPEC.md before any separately authorized phase.
Stopped after this repair phase; no next phase started.
```

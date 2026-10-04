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


# Follow-up: retry failed post-close scans

The earlier "one post-close attempt" behavior was a bug; this follow-up supersedes that description and its test expectations. A day is now done only after a successful scan at/after its final-scan target. Startup and manual successes satisfy the requirement. Failures retain stale results and schedule another automatic attempt after POST_CLOSE_RETRY_INTERVAL_SEC (default 300 seconds), measured from completion. The existing session-date gating remains. Results/status expose that pending retry through next_refresh_at.

Modified files: .env.example, app/core/config.py, app/scheduler/runner.py, app/services/state.py, docs/SPEC.md, tests/test_repairs.py, docs/REPAIR_REPORT.md. No blockers. Assumption: five minutes is the default minimum automatic retry delay; manual refresh retains its existing cooldown.

Commands executed: CodeGraph exploration; Get-Content docs/SPEC.md/config/report; targeted pytest; full pytest; git diff/check/status; rg documentation references; verified workspace-only temporary-directory cleanup.

```text
.venv/Scripts/python.exe -m pytest tests/test_repairs.py tests/test_scheduler.py -q -p no:cacheprovider --basetemp=.post-close-test-1 --tb=short
32 passed in 2.66s
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.post-close-test-2 --tb=short
104 passed, 1 warning in 3.25s
git diff --check: exit 0
```

VERIFIED: actual scheduler loop for pre-/post-close startup failures; repeated failures at configurable 90/300-second intervals; no premature done flag; stop after successful retry; manual success recognition; retry metadata and positive config validation; full offline suite.
NOT VERIFIED: live Yahoo recovery or live market operation. One existing Starlette/AnyIO deprecation warning remains.

| SPEC section | Status | Note |
|---|---|---|
| 0 Operating rules | Done | One focused correction; spec re-read and results recorded. |
| 5 Configuration | Done | Positive POST_CLOSE_RETRY_INTERVAL_SEC=300, exposed in env example. |
| 15 Refresh | Done | Successful completion required; failed scans retry with completion-based backoff. |
| 17 API | Done | next_refresh_at reflects the pending post-close retry. |
| 20 Tests | Done | Fake-clock failure/recovery/backoff regressions; 104 tests passed. |

```text
STATE SUMMARY
Phase completed: post-close retry correction.
.env.example: add default retry interval.
app/core/config.py: validate positive POST_CLOSE_RETRY_INTERVAL_SEC.
app/scheduler/runner.py: use success timestamps; backoff; success-only done flag.
app/services/state.py: expose pending retry deadlines after failed scans.
docs/SPEC.md: require retry until post-close success within the session date.
tests/test_repairs.py: failure, recovery, custom backoff and metadata regressions.
docs/REPAIR_REPORT.md: correction evidence and handoff.
Public signatures unchanged: Scheduler.execute_scan(market='NSE') -> bool;
ScanStateManager.get_next_refresh_at(market: str) -> Optional[datetime].
Config added: POST_CLOSE_RETRY_INTERVAL_SEC=300 (>0).
Decision: retry delay measured from failed completion; manual cooldown unchanged.
Known issues: upstream deprecation warning; live recovery unverified.
Next step: re-read docs/SPEC.md before any separately authorized phase.
Stopped after this correction; no next phase started.
```

# Live verification: prepared, awaiting real session

Requested: one real NSE scan during market hours and one after close. Preparation on Sunday, 2026-10-04 at approximately 11:08 IST found the exchange closed. No real scan has run in this phase. The existing real universe loads 501 constituents. The application's XBOM calendar proxy gives the next session as Monday, 2026-10-05, 09:15–15:30 IST.

Created `scripts/live_verify.py`; modified this report. The helper uses SystemClock and the configured real provider/universe, executes exactly one NSE scan through Scheduler.execute_scan, and captures the validated HTTP API responses in `logs/live_verification/<session>_<mode>.json`. It rejects the wrong window, retains existing attempts, and checks a new success timestamp, current-session freshness, session_partial flags and the post-close refresh deadline. Background scheduling is disabled for this verification process. Successful scans also update the application's normal snapshot/history. API requests use the in-process ASGI transport; this does not verify browser behavior or a listening server.

Created and inspected active chat follow-up `verify-real-nse-scans`, scheduled for weekdays at 11:00 and 16:00 IST. Intended first pair: Monday, October 5. The follow-up checks the actual exchange window, skips holidays/missed windows, records each attempt once and pauses after the pair or a failure. No automatic repeated live scans are authorized by this follow-up. Local execution requires the computer and app running, plus Yahoo network access. Timing is a scheduled intention, not executed evidence.

Commands/actions executed: full docs/SPEC.md read; CodeGraph exploration; clock/calendar and real-universe preflight; existing automation inspection; helper execution in both modes; py_compile; automation creation/view; git status and git diff --check. Final helper check output:

```text
NSE market-hours: 2026-10-04T11:08:37.182761+05:30, in_window=False
Deferred: no Yahoo requests made outside the requested window.
market-hours exit: 2
NSE post-close: 2026-10-04T11:08:38.804992+05:30, in_window=False
Deferred: no Yahoo requests made outside the requested window.
post-close exit: 2
compile exit: 0
```

VERIFIED: real local calendar/universe preflight; Sunday window refusal; Python compilation; active follow-up configuration. NOT VERIFIED: live Yahoo access, either requested live scan, live session freshness, browser behavior or live failure recovery. No synthetic or clock-shifted scan is counted as live evidence. No additional automated test suite was run for this verification helper.

Assumptions: NSE is the requested market; use the existing user-provided universe and configured thresholds. A post-close verification must start after the configured close delay, not during pre-open or on a non-session day. Each mode records one attempt; failures require attention rather than silently passing.

| SPEC section | Status | Evidence |
|---|---|---|
| 0 Operating discipline | Done for preparation | Spec re-read; one live-verification phase remains pending. |
| 1–2 Data/calendar integrity | Prepared | Real universe and SystemClock; wrong windows refused; XBOM proxy disclosed. |
| 13 session_partial | Pending live | Helper validates flags against actual session time. |
| 15 Refresh | Pending live | One real worker scan per window; final deadline checked after success. |
| 17 API | Pending live | Results/status response validation included in future capture. |

```text
STATE SUMMARY
Phase: requested live verification, pending actual session.
Created: scripts/live_verify.py.
Modified: docs/REPAIR_REPORT.md.
Unrelated existing changes preserved.
Live Yahoo requests in this phase: zero.
Real universe preflight: 501 constituents loaded.
Actual current time: Sunday 2026-10-04, NSE closed.
Next calendar session: Monday 2026-10-05 09:15–15:30 IST.
Follow-up: verify-real-nse-scans, ACTIVE, this chat.
Intended checks: Monday 11:00 IST and 16:00 IST.
Helper checks: both correctly deferred (exit 2); py_compile exit 0.
Evidence destination: logs/live_verification/<session>_<mode>.json.
Success criteria: actual scan success, fresh session data, correct metadata.
Known limitation: requires running local computer/app and Yahoo access.
Live verification is NOT complete; no live pass claimed.
Next scheduled run: re-read spec, inspect existing evidence, run eligible mode.
Stop condition: both attempts completed, or a failure needs user attention.
```

# UI improvement: light research workspace

Completed the user's UI request with the existing light theme: white surfaces, blue accents, neutral borders, compact metric cards and a simpler header. Removed the remote font import and technical environment-variable wording from the main screen. Retained HTTP-only access, market selection, timed polling, stale warnings, delayed-data disclosure, scan/settings controls and the research disclaimer.

The qualified list now shows every API result by default. Search, partial-session filtering and optional indicator filters only narrow that list; disabling the indicator filters restores all qualified results. Volume filtering has no fixed upper cap, and P/E filtering can include non-positive values. Added explicit sorting, a compact highest-ranked summary, optional detailed volume columns, exchange-local timestamps, clear empty states, filtered CSV containing all original result fields and white chart backgrounds. Diagnostics stay on a separate tab. The original white/blue theme palette is preserved. Corrected Streamlit's usage-statistics setting to the supported browser section.

Files modified: `ui/app.py`, `.streamlit/config.toml`, `tests/test_ui.py`, `scripts/live_verify.py`, `docs/REPAIR_REPORT.md`. Created: `tests/fixtures/ui_results_synthetic.json` (explicitly labelled, test-only fictitious companies); runtime screenshot `logs/ui/light-ui-preview.png` (ignored by Git). No market data, backend screening thresholds, scheduled cadence or installed dependencies were changed.

Related correction for the already-authorized pending live verification: the API's data_as_of is an exchange-local ISO timestamp, not a bare date. The verification helper now compares its date prefix with the actual session date. The previous guard would falsely reject valid fresh data. This correction does not constitute a live scan or live verification pass.

Commands/actions: full spec read; CodeGraph UI/client/tests exploration; installed Streamlit API signature inspection; targeted UI pytest; full pytest; py_compile; browser review using the existing local services; git diff/check/status; verified cleanup of workspace-only test folders. Attempted new local service launches found ports 8000/8501 already occupied, so the running user services were reused. No additional service was left running and no scan was triggered during browser QA.

Actual final verification output:

```text
.venv/Scripts/python.exe -m pytest tests/test_ui.py tests/test_repairs.py::test_streamlit_renders_real_countdown_without_network -q -p no:cacheprovider --tb=short
7 passed in 2.38s
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.ui-final-tests --tb=short
107 passed, 1 warning in 3.97s
py_compile scripts/live_verify.py ui/app.py: exit 0
```

An initial full-suite run had 99 passing tests and eight temporary-directory permission errors under .pytest_cache; using a fresh workspace test folder resolved those errors. The remaining warning is the existing Starlette/AnyIO deprecation. Automated interaction coverage includes full results by default, negative P/E filtering, high volume ratios, exchange-local time, sorting, enabling/disabling filters, search and no-match rendering. Synthetic responses are restricted to the AppTest process; the production preview uses the existing HTTP API and saved real results.

VERIFIED: 107 offline tests; actual browser rendering of the sidebar, results and light charts; search narrowing 18 saved results to the matching company; sorting interaction; computed app background rgb(255,255,255) and text rgb(33,37,41); no frontend imports of backend internals. Reviewed narrow/default and desktop layouts; restored the temporary viewport override. NOT VERIFIED: fresh live Yahoo scans or all browser/device combinations. The scheduled live pair remains pending. The visible saved scan was already available from the user's running service; it is not counted toward that pair.

Assumptions: preserve Streamlit and the existing light palette; UI filters affect the view, not backend screening; CSV exports all fields for currently visible results even when optional table columns are hidden. No blockers.

| SPEC section | Status | Note |
|---|---|---|
| 0 Operating rules | Done | Spec re-read; scoped UI improvement and verification evidence recorded. |
| 2 Data integrity | Done | Fictitious data restricted to labelled test fixtures; browser uses existing API data. |
| 3 Stack | Done | Existing Streamlit/pandas/Plotly; no new dependencies. |
| 9 session_partial | Done | Flags retained, filter and incomplete-volume explanation visible. |
| 16 Market status | Done | Open/closed, exchange time, holiday awareness and delayed-data notice. |
| 17 API | Done | Existing HTTP contract preserved; timestamp display/verification use actual format. |
| 18 Streamlit UI | Done | Light theme, table/search/sorting, filters, CSV, refresh/countdown, stale banner and footer. |
| 20 Tests | Done | UI interactions and full suite executed; 107 passed. |

```text
STATE SUMMARY
Phase completed: light UI improvement.
ui/app.py: lighter layout, optional filters, sorting, local times, white charts.
.streamlit/config.toml: retain light theme; move usage-statistics setting to browser.
tests/test_ui.py: optional filters, timestamps, actual Streamlit interaction checks.
tests/fixtures/ui_results_synthetic.json: labelled fictitious UI data for tests only.
scripts/live_verify.py: compare ISO data_as_of date with actual session date.
docs/REPAIR_REPORT.md: UI changes, verification and limitations.
logs/ui/light-ui-preview.png: browser preview, ignored runtime artifact.
Public: filter_results_dataframe(df, search_query, min_rsi, min_vol_ratio, max_pe,
partial_only) -> pd.DataFrame; three numeric filters now also accept None.
Public: format_scan_time(value: Optional[str], market: str) -> str.
main() and live_status_and_countdown_fragment(market) signatures preserved.
results_fragment parameters preserved; numeric filter types now Optional[float].
Backend APIs unchanged; no new threshold/config keys or dependencies.
Decision: display all API-qualified stocks unless users narrow the view.
Decision: preserve the existing light theme; charts explicitly use white backgrounds.
Tests: final 107 passed, one existing upstream deprecation warning.
Browser QA: real saved results, sidebar, search, sorting and charts reviewed.
Live scans: still NOT VERIFIED; existing scheduled follow-up remains active.
Next phase first step: re-read docs/SPEC.md before further authorized changes.
Stopped after UI improvement; no additional development phase started.
```

# Feature improvement audit and implementation scope

Date: 4 October 2026. Superseded by the user's later priority: signal outcome
tracking. See `docs/SIGNAL_OUTCOME_PLAN.md` for the first implementation scope.
Rank explanations and stock comparison are deferred follow-ups.
The ordered roadmap is outcome tracking, then the NSE context layer described
in `docs/NSE_CONTEXT_PLAN.md`, then evidence-based scoring changes.
This document is a reviewed implementation plan, not a claim that the features
are implemented. No application code was changed during this audit.

## Existing behavior verified from source

- Results expose price, total score, RSI, observed/average volume, volume ratio,
  trailing P/E, market, bar date and partial-session status.
- The UI supports search, optional filters, sorting, six-row pagination, cards,
  CSV export, distribution charts, scan funnel and data-issue records.
- The backend calculates a weighted score but returns no component explanation.
- Scoring thresholds, caps and weights come from backend configuration. The UI
  has no HTTP representation of the scoring settings used for a saved scan.
- Atomic JSON snapshots retain the latest scan. SQLite stores historical totals
  and metrics, but not the scoring configuration or component contributions.
- There is no dedicated stock comparison view in the current UI.
- Backend rank ordering rounds scores to six decimals before its tie-breaks;
  the current UI sorts the displayed view by raw scores. The feature must give
  ranks consistent with the backend, particularly near score ties.

## First implementation scope: explanations and comparison

### Stock explanation

Add an on-demand stock details view, keeping the current compact screener as the
default. It will show:

1. Rank in the scan, ticker/name, data session, freshness and partial-bar status.
2. Volume, RSI and valuation components: normalized value, weight and weighted
   contribution to the total score. Display precision must not change ranking.
3. The actual thresholds, caps and weights captured for that successful scan.
4. The screening rules that a returned stock passed. The available payload does
   not contain previous-day RSI; a trend-rule explanation must report the
   backend's recorded outcome or mark the underlying value unavailable.
5. Why a non-positive P/E contributes zero, while remaining a valid result.
6. The bounded volume-projection explanation for partial sessions, alongside
   observed volume and its completed-session average.

The component display explains this screener's score. It will not label that
score as a forecast or invent buy/sell recommendations.

### Compare stocks

Add a comparison tab with a selector for two to four stocks from the same
market's latest returned results, across all pages. Display columns per stock
and rows for rank, score, price/currency, RSI, volume ratio, observed/average
volume, P/E, session date, partial flag and component contributions when known.

Selection is keyed by ticker and market. Sorting, pagination and periodic
refresh must not silently select a different stock. A stock that disappears
from the latest returned results is reported as unavailable and is not replaced.
NSE and NYSE selections remain separate. The view handles zero, one, two and
four selections and offers a CSV export of the selected comparison.

### Backend and persistence

- Refactor the existing scoring formula into one source of truth that produces
  both the total and its breakdown. Preserve all current formulas, thresholds,
  clipping, non-positive P/E behavior and deterministic tie-breaks.
- Capture explanation data and configuration with a successful scan. Extend
  `GET /api/results` additively with optional explanation fields. The UI remains
  an HTTP client and does not import or duplicate backend scoring logic.
- Persist explanation data in the versioned JSON snapshot and restore it on
  startup. Preserve old snapshot and SQLite restore compatibility.
- For older scans without recorded scoring context, show the original metrics
  and total with "Score breakdown unavailable for this saved scan." Do not
  reconstruct contributions using today's configuration.
- Extend the spec's API/persistence descriptions to document the additive fields
  within the same implementation phase; no screening requirement is removed.
- No new market data provider, live feed, dependencies or thresholds are needed.

## Acceptance checks

- Contributions sum to the total within floating-point tolerance; displayed
  rounded contributions are identified as rounded.
- Independent formula tests cover caps, boundaries, alternate weights, optimum
  P/E, and zero/negative P/E. Existing ranking tests continue to pass.
- Serialization, snapshot restore and legacy fallback preserve real historical
  totals and do not fabricate explanation data.
- API errors, empty scans, stale results and partial-session caveats remain clear.
- UI tests cover comparison selection across sort/page changes, market switches,
  disappearing stocks, duplicate selections and comparison export contents.
- Browser checks cover the compact default screen, stock details, comparison
  and narrow-screen scrolling using the already saved real results.
- No live Yahoo scan is needed for test validation; synthetic test inputs remain
  confined to tests and are never served by the production API/UI.

## Expected files for implementation

`app/core/ranking.py`, `app/core/interfaces.py`, `app/services/scanner.py`,
`app/services/state.py`, `app/api/models.py`, `app/cache/persistence.py`,
`ui/app.py`, `ui/styles.css`, `docs/SPEC.md` and relevant ranking/API/persistence/UI
tests. Final scope may use a focused UI helper module to keep `ui/app.py` readable.
These are planned edits, not modifications made by this audit.

Later candidates, outside this first phase: persistent watchlists/filter presets;
new/removed matches across completed scans; historical validation of screening
signals. Each requires a separately scoped follow-up.

## Audit commands and real test output

Read `docs/SPEC.md` and `README.md`; inspect the docs directory; inspect current
Git status; explore UI, ranking, API and persistence through CodeGraph; read the
remaining UI tail not returned by the capped exploration.

```powershell
$taskFeatureAuditTemp = Join-Path (Get-Location).Path ('logs/feature-audit-tests-' + [guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp $taskFeatureAuditTemp
git diff --check
git status --short
```

```text
........................................................................ [ 64%]
.......................................                                  [100%]
111 passed, 1 warning in 5.46s
```

The warning is the existing Starlette/AnyIO BlockingPortal deprecation.
VERIFIED: current source behavior above and the baseline suite. NOT VERIFIED:
new explanations/comparison, because implementation has not begun; fresh Yahoo
data and browser behavior of the planned features. Assumption: the user's
selected priority defines the first feature phase, with the compact UI retained.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0 — Phase discipline | Done | Reviewable plan prepared; implementation awaits CONTINUE. |
| 2 — Data integrity | Done | No market data created or production scan triggered. |
| 5, 9, 12 — Configuration/indicators/ranking | Not in this phase | Behavior reviewed; proposed explanations preserve it. |
| 14, 17 — Persistence/API | Not in this phase | Additive implementation and legacy behavior specified. |
| 18 — UI | Not in this phase | Existing features reviewed; comparison scoped. |
| 20 — Tests | Done | Existing full suite executed, 111 passed. |
| 22 — Reporting | Done | Files, commands, evidence, assumptions and state recorded. |

## STATE SUMMARY

```text
Completed: feature audit and first implementation scope.
User priority: explain rankings and compare stocks.
Created docs/FEATURE_IMPROVEMENT_PLAN.md: scope, acceptance checks and evidence.
Application files modified: none.
Public classes/functions added or changed: none.
Config keys added: none.
Decision: backend-owned score breakdown captured with successful scans.
Decision: compare 2–4 stocks within one market using stable ticker selections.
Decision: old scans retain totals; unavailable breakdowns are labeled honestly.
Baseline tests: 111 passed, one existing dependency warning.
Known limits: planned features not implemented; no live data validation.
Next phase first step: after CONTINUE, reread docs/SPEC.md and implement the
documented additive scoring contract plus persistence before its UI consumers.
Stopped: awaiting explicit CONTINUE as required by AGENTS.md and SPEC section 0.
```
# Implementation update

Signal outcome tracking is implemented. See [implementation and verification](SIGNAL_OUTCOME_IMPLEMENTATION.md).
The NSE context layer remains the next phase; explain/compare features remain queued.


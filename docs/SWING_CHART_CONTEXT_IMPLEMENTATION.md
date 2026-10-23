# Recorded daily breakout charts

Implemented 22 October 2026, user-authorized P1 chart improvement only. Contract:
`docs/SWING_CHART_CONTEXT_CONTRACT.md`; master spec section 35.

## Result and provenance

Daily breakout details now present the prior resistance window/level, breakout
close ceiling and ATR allowance, recorded signal close, and observed share volume
against the frozen prior-session baseline. Captions give the full window dates,
session counts, signal volume/RVOL/floor, ATR period/value, breakout extension
above resistance and shared extension above the fast EMA. Existing entry minimum,
entry maximum, stop and target lines remain in place.

The backend adds optional typed `chart_contexts` response metadata, bound to plan
ID, ticker, signal session, aware decision time, vintage, known price basis,
configuration hash and consumed-data hash. Resistance and extension operands come
from the already evaluated checks. Dates come from the same consumed history;
both prior windows exclude the signal. ATR, average volume and RVOL come from
frozen plan features, and periods/limits come from frozen plan configuration.
No current UI setting or displayed-candle subset supplies an indicator value.

This metadata is outside immutable Plan and ledger records. Old responses default
to an empty context list; no historic chart metadata is reconstructed or rewritten.
Missing older rule checks produce no context. Missing data, rejected setups and
pullback plans receive no breakout chart context. Source S1 unknowns stay unknown.
New evaluations acquire changed implementation fingerprints because source files
changed; existing frozen studies/reports still require their original code/runtime.

The UI checks plan/context bindings and chart ticker/vintage/known basis, including
per-row ticker/basis where present. Malformed dates, signal-inclusive windows,
missing fields and nonfinite values suppress new overlays and show unavailable
context. Unknown/mismatched charts suppress all plan overlays. The ticker check
also strengthens the pre-existing entry/stop/target overlay boundary.

Visual windows are clipped to available displayed bars, while captions retain the
full recorded window dates. Missing candles/volume remain missing. The resistance
and baseline lines are static recorded signal-session values, not rolling series.
The breakout ceiling is a signal-close rule, not an executable entry ceiling.
Charts remain research/inspection only and cannot change eligibility or orders.

## Files created / modified in this phase

| File | Purpose |
|---|---|
| `app/swing/charts.py` | Frozen context records and presentation of reached rule/plan values |
| `app/swing/service.py` | Attach context only to returned daily breakout plans |
| `app/swing/api.py` | Typed additive contexts with an empty default for older payloads |
| `app/swing/setups.py` | Include chart metadata implementation in the existing source fingerprint |
| `app/swing/workspace.py` | Explicit empty contexts for disabled research |
| `ui/swing_charts.py` | HTTP-only identity checks, chart overlays, volume subplot and captions |
| `ui/swing.py` | Pass selected plan context to chart rendering and display its captions |
| `tests/test_swing_charts.py` | Numeric provenance, no-lookahead, response compatibility and overlay boundaries |
| `tests/fixtures/swing_ui.py` | Labelled SYNTHETIC context and consistent observed-bar fixtures |
| `tests/test_swing_ui.py` | AppTest integration and old-response unavailable state |
| `docs/SPEC.md` | Authorized section 35 |
| `README.md` | Feature description/report reference |
| `docs/SWING_CHART_CONTEXT_CONTRACT.md` | Scope and acceptance contract |
| `docs/SWING_CHART_CONTEXT_IMPLEMENTATION.md` | This report |
| `docs/swing/CHART_REAL_CHECK.json` | Real HTTP, browser and protected-file evidence |

Earlier P1 explanation and verification-reuse changes remain uncommitted in the
same working tree and were preserved. No dependency or environment key was added.
Ignored local helper `logs/chart_real_check.py` records the actual-archive probe;
the existing `logs/swing_s5_preview.py` and `logs/p1_swing_ui.py` were reused without
edits. Temporary test stores/logs are under `logs/chart-focused` and `logs/chart-full`.

## Commands and executed tests

Read the complete master spec, AGENTS and S2/S5 contracts before implementation.
Used Graphify's existing graph and current CodeGraph source, without rebuilding.
Vocabulary expansion: `swing chart features plan configuration volume baseline atr
extension snapshot`. The broad graph traversal was capped; CodeGraph confirmed
current source and the exact rendering/evaluation paths. Query/model token cost
is unavailable; no token savings or monetary cost claim is made.

```powershell
graphify reflect --if-stale
graphify query 'swing chart features plan configuration volume baseline atr extension snapshot' --dfs --budget 1800
.venv/Scripts/python.exe -m pytest tests/test_swing_charts.py tests/test_swing_ui.py tests/test_swing_explanations.py tests/test_swing_api.py -q -p no:cacheprovider --basetemp logs/chart-focused/pytest --tb=short -o faulthandler_timeout=60
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp logs/chart-full/pytest --tb=short -o faulthandler_timeout=60
.venv/Scripts/python.exe logs/p1_compare_behavior.py
.venv/Scripts/python.exe -m uvicorn logs.swing_s5_preview:app --host 127.0.0.1 --port 18775 --log-level warning
# API_BASE_URL=http://127.0.0.1:18775 for this preview process only:
.venv/Scripts/python.exe -m streamlit run logs/p1_swing_ui.py --server.address 127.0.0.1 --server.port 18575 --server.headless true --browser.gatherUsageStats false
.venv/Scripts/python.exe logs/chart_real_check.py
git diff --check
```

TEMP/TMP used the corresponding workspace log directory. Offline test execution
and loopback preview used approved execution outside Windows sandbox limitations;
live tests remain excluded. Installed Plotly **7.1.0** rendered/serialized the
subplot specification; no package installation was needed.

| Check | Actual result |
|---|---|
| Focused backend/API/chart/UI tests | **`63 passed, 1 warning in 32.08s`**, exit 0 |
| Full offline suite | **`518 passed, 1 warning in 238.82s (0:03:58)`**, exit 0; includes 27 new chart/UI cases |
| Original-evaluator comparison | **70 SYNTHETIC scenarios**, Plan/Rejection fields identical with normalized implementation fingerprints; exit 0 |
| Real HTTP probe | Exit 0; 497 members, 0 plans, 0 contexts; unknown-basis chart has 116 observations and no overlays; mutations blocked with 403 |
| Protected-file comparison | Existing observation/history and optional ledger/control/validation/forward DB/WAL hashes unchanged; absent stores remain absent |
| Browser | Actual archive workspace and AARTIIND.NS source chart loaded; “Price basis: Unknown · 4 missing sessions · Inspection only” remained visible |

Regression coverage includes configurable exact window lengths/dates, exclusion
of signal and future bars, original rule operands, both ATR extension meanings,
all eight context identity/hash bindings, wrong ticker/vintage/basis, empty/older
metadata, nonfinite/invalid windows, preserved four levels, clipped chart subsets,
missing volume, separate pullback/source behavior, complete JSON serialization,
HTTP-client-only AppTest rendering and no mutation during inspection.

## Assumptions, VERIFIED and NOT VERIFIED

Assumption: “ATR extension” should show both existing meanings explicitly: breakout
distance from prior resistance and shared distance from fast EMA. Neither changes
the existing inequalities or limits. The volume line is the recorded signal's
prior-session baseline, not a newly calculated moving average.

VERIFIED: recorded numeric/window provenance and Plotly specifications in isolated
tests; UI payload/caption integration; real HTTP unknown-basis behavior; real
browser empty/source inspection states; 70-scenario calculation equivalence and
protected-store hashes. Synthetic data was confined to offline tests/AppTest and
was never served by the real preview API/UI.
The suite's sole warning is the existing Starlette TestClient use of AnyIO's
deprecated `BlockingPortal` alias. Temporary API/UI preview processes and their
browser tab were stopped after verification.

NOT VERIFIED: a populated real breakout chart in the browser, because current
archive source gaps return no candidates; publisher authenticity, an executable
edge or long-term chart usability. No broker/account/import/source-repair action,
strategy promotion, deployment or commit was performed.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0, 22 — phase/report discipline | Done | One authorized improvement, evidence and handoff; stop afterward |
| 2, 3, 5 — integrity/stack/config | Done | No fabricated live data, dependencies, new thresholds or settings |
| 17, 18 — API/UI | Done | Additive typed metadata; HTTP-only UI and unavailable states |
| 20 — tests | Done | 518 offline tests passed, including 27 new chart/UI cases |
| 26 — provenance | Done | Exact vintage/basis/identity binding; unknowns and gaps retained |
| 27 — daily strategies | Done | Original formula/plan/rejection comparison passed; no rule changes |
| 28, 29, 31 — lifecycle/validation | Done | Stored schemas/frozen records and execution gates unchanged |
| 30, 33 — chart/workspace explanations | Done | Recorded chart evidence, compatible levels, no synthetic live demo |
| 32 — source repair | Not in this phase | Current data gaps remain |
| 34 — verification reuse | Done | Existing request scopes retained and covered by regression suite |
| 35 — recorded breakout charts | Done | Resistance/window, volume baseline, ATR extensions and fallback |

## STATE SUMMARY

```text
Phase completed: P1 recorded daily breakout chart context.
app/swing/charts.py: context records/builder, outside Plan and ledger.
app/swing/service.py: attach contexts; app/swing/api.py: optional typed list.
app/swing/setups.py: source fingerprint includes charts.py.
app/swing/workspace.py: disabled context list is empty.
ui/swing_charts.py: guarded plots/captions; ui/swing.py: selected-plan integration.
tests/test_swing_charts.py: evidence and overlay regressions.
tests/fixtures/swing_ui.py: SYNTHETIC chart fixture.
tests/test_swing_ui.py: HTTP-payload integration and older-response fallback.
docs/SPEC.md: section 35; README.md: feature reference.
docs/SWING_CHART_CONTEXT_CONTRACT.md: acceptance contract.
docs/SWING_CHART_CONTEXT_IMPLEMENTATION.md: implementation/evidence report.
docs/swing/CHART_REAL_CHECK.json: actual archive verification.
Public backend: ResistanceWindow, VolumeBaseline, AtrExtension, BreakoutChartContext.
breakout_chart_context(plan: Plan, rows: tuple[DailyPrice,...], checks: tuple[RuleCheck,...]) -> BreakoutChartContext | None.
Public UI: chart_figure(payload, plan=None, context=None) -> Figure | None.
matched_breakout_context(payload, plan, context) -> dict | None.
chart_context_captions(payload, plan, context=None) -> tuple[str,...].
Config/dependencies/DB schema added: none; Plan unchanged; response chart_contexts defaults [].
Decisions: static signal-session evidence, both prior windows exclude signal.
No UI indicator recomputation; strict plan/hash/ticker/vintage/known-basis matching.
Known issue: zero real candidates; populated browser overlay check unavailable.
Tests: 518 passed, 1 existing dependency warning; 70 behavior comparisons passed.
Prior phase changes preserved; no commit/deployment/financial mutation.
Next step: await explicit CONTINUE, then reread docs/SPEC.md for next authorized phase.
```

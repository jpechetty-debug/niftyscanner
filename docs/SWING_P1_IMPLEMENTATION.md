# P1 — rule explanations and accurate empty states

Implemented 22 October 2026, P1 only. Contract:
[SWING_P1_CONTRACT.md](SWING_P1_CONTRACT.md); master spec section 33.

## Result

Trade Ideas now distinguishes normal technical-rule rejections, data/evaluation
failures, partial failures, candidates hidden by view filters, disabled research
and missing evaluation metadata. Independent source/validation requirements remain
visible without being presented as the cause of every empty result list.

The backend captures observed operands, operators, actual thresholds and outcomes
for reached shared/setup/entry-window/stop/reward-risk checks. An expandable stock
inspector presents these values for candidates and rejected stocks, scoped to the
selected strategy when requested. Compound rules explain pairwise/any/all tests.
Displayed values are rounded; pass/fail uses the original unrounded calculation.

Required history or availability failures produce no invented indicators. Missing
or nonfinite explanatory values remain unavailable. No later stage is claimed to
have been evaluated after the existing early return. The two daily hypotheses
remain independent; a failed check in one does not negate a candidate in the other.

Explanations are response metadata, separate from immutable Plan/Rejection and
ledger records. Older responses without explanations remain compatible and show
that details are unavailable. New evaluations have new implementation fingerprints
because source files changed; historical records/fingerprints are not rewritten.

## Files created / modified

| File | Change |
|---|---|
| `app/swing/explanations.py` | New frozen rule/explanation records, finite-value capture and server view summaries |
| `app/swing/plans.py` | Add optional checks to Evaluation; Plan and Rejection schemas unchanged |
| `app/swing/setups.py` | Capture existing rule operands/outcomes; preserve inequalities, calculations and priority |
| `app/swing/service.py` | Attach per-ticker explanation metadata to research reads |
| `app/swing/api.py` | Typed additive explanations with an empty default for old payloads |
| `app/swing/workspace.py` | Derive view state before applying view filters; distinguish disabled research |
| `ui/swing.py` | Render server empty states, counts and stock rule inspector through HTTP payloads |
| `tests/fixtures/swing_ui.py` | Extend labelled SYNTHETIC HTTP-only UI fixtures |
| `tests/test_swing_explanations.py` | Numeric, boundary, stage, unavailable, summary, workspace and API-read serialization checks |
| `tests/test_swing_ui.py` | Correct filtered-view expectation; add distinct empty-state/rule-table checks |
| `docs/SPEC.md` | User-authorized P1 contract, section 33 |
| `docs/SWING_P1_CONTRACT.md` | Scope and acceptance frozen before implementation |
| `docs/SWING_P1_IMPLEMENTATION.md` | This implementation/verification report |
| `docs/swing/P1_REAL_CHECK.json` | Recorded real-archive HTTP/state checks, no synthetic examples |
| `README.md` | Feature and verification reference |

Ignored local helpers: `logs/p1_compare_behavior.py` (pre-P1 semantic comparison),
`logs/p1_swing_ui.py` (read-only preview UI), `logs/p1_real_check.py` (real HTTP
verification). The existing `logs/swing_s5_preview.py` was reused unchanged.
Workspace-local pytest temporary files remain under `logs/`.

## Commands and real test results

Read AGENTS and the complete master spec, S2/S5 contracts and Graphify lessons.
Used Graphify's existing research vocabulary/graph and CodeGraph's current source
before edits; no graph rebuild or additional extraction API calls. Query/model
token accounting is unavailable; no token savings or monetary costs are claimed.

Pytest used unique workspace-local TEMP/TMP/basetemp directories, the project
venv and `-p no:cacheprovider --tb=short`. Commands:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_swing_setups.py tests/test_swing_ui.py -q -p no:cacheprovider --basetemp <workspace-temp>/pytest --tb=short
.venv/Scripts/python.exe -m pytest tests/test_swing_explanations.py tests/test_swing_setups.py tests/test_swing_ui.py -q -p no:cacheprovider --basetemp <workspace-temp>/pytest --tb=short
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp <workspace-temp>/pytest --tb=short -o faulthandler_timeout=60
.venv/Scripts/python.exe logs/p1_compare_behavior.py
.venv/Scripts/python.exe logs/p1_real_check.py
git diff --check
```

| Execution | Actual result |
|---|---|
| Setup/UI baseline | `35 passed in 13.30s` |
| Existing setup/UI tests after implementation | `35 passed in 13.89s` |
| First completed expanded target run | `1 failed, 55 passed in 18.08s`; the new workspace stub supplied two candidate plans while asserting one |
| Corrected fixture + full regression | **`467 passed, 1 warning in 194.72s (0:03:14)`**, exit 0 |
| Pre-P1 behavior comparison | **70 SYNTHETIC scenarios**, plan/rejection fields identical after normalizing implementation fingerprints |
| Real HTTP verification | 497 explanations, zero plans; unavailable rules not fabricated, mutation route 403, protected stores unchanged; exit 0 |

The one warning is the existing Starlette/AnyIO BlockingPortal deprecation.
The first sandboxed expanded run was interrupted after it stalled at threaded
API checks. The completed target/full runs used the approved host execution
path. A sandboxed localhost probe also failed with Windows socket permission
error 10013; the approved host probe succeeded. These were verification-environment
limitations, not application test passes. No automatic approval rejection occurred.

The behavior comparison loaded the pre-P1 evaluator from Git HEAD and exercised
both daily hypotheses across price/volume changes, configuration boundaries and
insufficient history. Only implementation fingerprints were normalized. Plan
metrics, levels, expiry, ranking inputs, rejection categories/reasons and all
other plan/rejection fields matched. It does not prove profitable signals.

## Real archive and browser verification

Used the actual real archive through an isolated API on 127.0.0.1:18775 and a
temporary Streamlit preview on 127.0.0.1:18575. Scheduler/outcomes/replay workers
were disabled, and middleware rejected all mutation requests. No live Yahoo
download, scan, account creation, fill, alert or model promotion was performed.

The real archive returned 497 failed evaluations with 497 explanation records
and no calculated checks for unavailable histories. The browser visibly showed:

- "Data or evaluation unavailable" and the failed-versus-filtered counts.
- Separate eligibility/validation requirements.
- The real stock selector and recorded missing-history/benchmark reasons.
- Explicit unavailable technical values, rather than guessed thresholds or zeros.

The populated numeric rule-table and ordinary/partial/filter-only empty states
were verified using AppTest with labelled SYNTHETIC fixtures only; those fixtures
were never served by the real preview. No separate live-data success case is
claimed because the source blockers established in P0 still exist.

The real probe checked before/after hashes for the observations, paper,
operations, validation, forward and legacy-history stores. Existing hashes
matched, and missing stores were not created. Preview services were temporary
and stopped after verification.

## VERIFIED / NOT VERIFIED

VERIFIED: full offline suite, typed read serialization, backend view counts,
strict/inclusive boundaries, unavailable values, compound-rule operands, no
rule calculation after failed prerequisites, HTTP-only UI tests, real browser
unavailable state, host read-only HTTP probe, protected-store hashes, legacy
ledger/replay/validation regressions and the pre-P1 semantic comparison.

NOT VERIFIED: repaired source readiness, a real-data qualifying trade idea,
live provider availability, independent strategy edge/profitability, prospective
capture completion, every browser viewport or performance under production load.
Assumption: missing source evidence retains its existing meaning; this phase
explains the current rules rather than changing eligibility.

## SPEC COMPLIANCE

| Sections | Status | Note |
|---|---|---|
| 0: discipline/reporting | Done | P1 only; subsequent work waits for CONTINUE |
| 2, 6–8, 19: data integrity | Done | No invented real rows, source repair, download or new universe |
| 17–18: typed API / HTTP-only UI | Done | Additive read metadata; UI does not import app logic |
| 20: tests | Done | Full suite and independent comparison executed |
| 27: frozen swing rules | Done | Inequalities, levels/costs/ranking retained; new fingerprints disclosed |
| 28–31: lifecycle / validation | Done | Existing schemas/records retained; regression coverage passed |
| 32: source repair | Not in this phase | P0 blockers remain visible |
| 33: P1 explanations | Done | Backend rule evidence and accurate empty states delivered |

```text
STATE SUMMARY
Completed: P1 accurate empty states and backend rule explanations.
New: app/swing/explanations.py — RuleCheck/RuleExplanation and view_summary.
Modified: app/swing/plans.py — Evaluation.checks only; Plan unchanged.
Modified: app/swing/setups.py — capture existing rule evidence/fingerprints.
Modified: app/swing/service.py and api.py — typed per-ticker read metadata.
Modified: app/swing/workspace.py — pre-filter counts and accurate view states.
Modified: ui/swing.py — empty-state messages and rule inspection.
Tests: fixtures/swing_ui.py, test_swing_explanations.py, test_swing_ui.py.
Docs: SPEC.md §33, SWING_P1_CONTRACT.md, SWING_P1_IMPLEMENTATION.md, README.md.
Evidence: docs/swing/P1_REAL_CHECK.json; ignored logs/p1_* helpers.
Public: RuleCheck(...), RuleExplanation(...), record_check(...)->None.
Public: view_summary(research, view_count, *, enabled=True)->dict.
UI: rule_value(value), rule_explanations_view(research, params).
Config/dependency keys added: none.
Tests: 467 passed, 1 existing warning; 70 semantic comparisons matched.
Data/ledger: no source import, new accounts, financial records or promotion.
Known limits: P0 source gaps; real candidate and profitability not established.
Next: reread SPEC and select one separately authorized improvement phase.
Stop: no later phase, commit, push or deployment performed.
```

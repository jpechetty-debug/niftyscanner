# S4 implementation and verification — 17 October 2026

S4 validation tooling is implemented. **No strategy has passed real validation.**
The actual archive still fails the data gate; no real account, capital, study or
terminal holdout was created. S5/S6 are not started.

## What changed

The [frozen protocol](SWING_S4_CONTRACT.md) and separate append-only registry
preregister complete configurations, every tested variant, explicit capital,
expanding train/validation/test folds and a future terminal holdout. Exact-session
embargo/purging protects holding-period labels. Tests must begin after registration;
overlapping terminal reservations for the same strategy are rejected. Terminal
opening commits a one-time claim before outcome access, so a crash consumes it.
Completed report reads are idempotent; there is no reset or holdout-retuning path.

The evaluator reads all relevant S3 accounts/plans/events under one SQLite
snapshot. It checks event/plan hashes, funding, configuration/implementation,
timely capture, source evidence, exact marks, unresolved paths, boundary crossings
and remaining liabilities. Each window/variant has a distinct flat-start account.
Repeated training dates are never pooled into OOS statistics. All captured
opportunities/rejections and invalid-window counts remain visible.

Metrics include net expectancy in INR/R, win/loss/payoff distribution, holding
duration, sector coverage, turnover, opening/closing exposure, participation,
calendar-marked NAV drawdown/underwater duration and fill/NAV coverage. Baselines
are gross Nifty 500, cash, a net daily-rebalanced fixed index/cash allocation and
a descriptive lagged-exposure index/cash comparison. Gross index versus estimated
net strategy and exposure differences are disclosed.

Seeded moving-block bootstrap resamples complete signal-date clusters including
zero-trade dates, without bridging separate windows. Reports show interval bounds,
independent-period estimates and empty resamples; sparse bootstrap replicates fail
the significance gate. Nearby distinct parameter configurations are frozen before
testing, and additional per-side costs stress the recorded filled cash flows.

Counterfactual delay/gap/halt portfolios reuse S3 risk, cash, capacity, adverse OHLC
and IOC rules in isolated memory. Frozen plan-ID sampling selects affected ideas.
Execution impact changes execution references, never observed OHLC. A temporary
exit halt can extend holdings only with additional verified observations; missing
paths stay unresolved. Derived event heads and policy hashes accompany results.
Production S3 references remain the observed prices by default; existing account
events are never rewritten.

Code, contract, dependencies/runtime, calendar and evaluated ledger heads are
fingerprinted. Optional dedicated HMAC-SHA256 authenticates manifests; actual
reports are explicitly UNSIGNED because no key was supplied. Tests verify signed
manifests using an isolated SYNTHETIC key. No credentials are generated or copied.
Recommendation status is separate from S2/S3 eligibility and never mutates plans.

## Files created/modified in S4

Created:

- `app/swing/validation_protocol.py`: protocol, environment defaults/bounds, calendar/purge design and fingerprints.
- `app/swing/validation_store.py`: registry, immutable reports, signatures and terminal claims.
- `app/swing/validation_stats.py`: NAV/baselines, distributions, bootstrap and statistical gates.
- `app/swing/validation_stress.py`: recorded-timeline counterfactual portfolios in memory.
- `app/swing/validation.py`: S3 reconciliation, source audit and validation recommendations.
- `scripts/swing_validate.py`: explicit schema/register/development/holdout/show/verify/audit commands.
- `tests/fixtures/swing_validation.py`, `tests/test_swing_validation.py`: SYNTHETIC fixtures and 38 new regression cases.
- This report, `docs/SWING_S4_CONTRACT.md`, `docs/swing/S4_DATA_AUDIT.json`, `docs/swing/S4_REAL_CHECK.json`.

Modified: `app/swing/replay.py` (identity reference hooks plus execution-reference
provenance), `.env.example`, `README.md`, `docs/SPEC.md` §29 and
`docs/SWING_TRADING_PLAN.md`. Earlier dirty S1–S3 work and user changes to the root
redirect/API test are preserved. No commits, pushes, dependency installations or
source-project edits were performed.

## Commands and actual results

Read `AGENTS.md` and all of `docs/SPEC.md` before starting S4. Used CodeGraph
before locating/editing existing replay/ledger/configuration integration. Commands
used the project virtual environment; pytest temporary directories were unique
`logs/swing-s4-*` directories, with no cache provider or external requests.

| Command | Actual result |
|---|---|
| `.venv/Scripts/python.exe -u -m pytest -q -p no:cacheprovider --basetemp <unique logs/swing-s4-baseline-…> --tb=short` | **356 passed**, 1 existing warning, 158.43 s |
| Same pytest command with `tests/test_swing_validation.py tests/test_swing_replay.py` | **57 passed**, 26.00 s |
| Same pytest command with `tests/test_swing_validation.py` after search/runtime/concurrency/audit additions | **38 passed**, 18.54 s |
| Final full-suite command, unique `logs/swing-s4-full-…` | **394 passed**, 1 existing warning, 165.82 s |
| `.venv/Scripts/python.exe -u logs/swing_s4_real_check.py` | Explicit bounded real audit; data hashes unchanged; no account/registry created |
| `python -m scripts.swing_validate audit --vintage 54e87b147932a02732f6931adf6fb8067c9a4f2d2084126e816f1d94a97d10aa --output docs/swing/S4_DATA_AUDIT.json` (invoked by the helper above) | **RESEARCH_ONLY**, UNSIGNED, six explicit data blockers |
| `git status --short`, `git diff --stat`, `git diff --check` | Existing dirty work retained; no whitespace errors, only existing LF/CRLF notices |

The initial bootstrap-width regression used a synthetic 20-session cycle exactly
matching the resampling block. Its expectation was invalid; a 40-session dependence
fixture now checks the intended wider uncertainty versus independent resampling.
No real strategy thresholds were tuned from that test.

Final output:

```text
394 passed, 1 warning in 165.82s (0:02:45)
```

The warning is the existing Starlette TestClient deprecation of
`anyio.abc.BlockingPortal`; it is unrelated to S4. Final local checks also verified
new-file syntax/whitespace, environment-default consistency, manifest/code hashes,
report links and the <=40-line state block. The final real-audit refresh again
reported `stores_unchanged: true`, `model_status: RESEARCH_ONLY`,
`statistical_validation_performed: false`.

## Real evidence and limits

See [the data audit](swing/S4_DATA_AUDIT.json) and
[the read-only check](swing/S4_REAL_CHECK.json). The audit reverified the immutable
S1 vintage/artifact/universe hashes. It found 497 eligible current constituents,
241 symbols with prices, zero complete trailing histories and no configured
Nifty 500 benchmark rows. Publication, adjustment/identity, historical membership
and execution-context proof remain unavailable. S1 schema v1 cannot certify those
historical facts; the evaluator explicitly blocks them rather than accepting
client proof booleans. A richer verified provider/evidence contract is required
before that source gate can pass.

`data/swing.db` SHA256 remained
`506fff77cb8127dba8a3e075da0417bd3d7b5b57a6f7f149494f109476d3d57e`.
The real paper and validation databases remained absent. Real fills: **0**.
Real statistical evaluations/promotion: **none**. No historical dataset is claimed
untouched, and no made-up capital or dates were registered.

Assumptions: NSE long-only delivery; completed daily decisions; explicit INR
capital; frozen S2/S3 policies and estimated percentage costs. Current defaults
are research proposals, not thresholds mandated by an institution. Baseline cash
earns zero; the index is a gross price index. Counterfactual stresses are scenarios,
not observations or guaranteed fills. Bootstrap intervals approximate dependence;
regimes are not independently classified. XBOM special-session accuracy, source
licensing, actual tradability and future returns remain unverified. Relative paths
retain existing configured-working-directory semantics. S6 elapsed forward time
cannot be replaced by historical/synthetic simulation.

## VERIFIED vs NOT VERIFIED

| VERIFIED by executed checks | NOT VERIFIED / blocked |
|---|---|
| Frozen search budget, nearby-parameter validation, purge/embargo and exact-session rules | Real strategy profitability or institutional readiness |
| HMAC/hash tamper checks, immutable schemas, protected paths, concurrent one-shot claims | Independent auditor signatures; actual authentication key was absent |
| Development excludes terminal accounts; completed report reads reproduce | Actual untouched prospective cohorts or sufficient real samples |
| Cluster uncertainty, sparse samples, costs/baselines/drawdown/exposure gates | Bias-free PIT membership/publication/adjustment and missing Nifty 500 data |
| Isolated delay/gap/halt paths; unknown extra bars remain unresolved | Actual opening capacity, halts/circuits and corporate-action completeness |
| Real S1 store hash preserved; no live/account mutation | Live market-hours/provider checks and S6 calendar time |

## SPEC COMPLIANCE

| SPEC section / invariant | Status | Evidence |
|---|---|---|
| 0/21/22: one phase, full spec read, commands/tests/report | Done for S4 tooling | This report and state block; S5/S6 stopped |
| 2/6/26: no fabricated market data; explicit evidence/universe | Done; real gate blocked | Test-only SYNTHETIC fixtures; unchanged real S1 store; six source failures |
| 3/5/7/8: source/config/provider/Clock separation | Done | No network/new dependencies; environment gates, recorded event clock and existing provider protocol |
| 14/15/17/18/28: store/worker/API/UI separation | Done within S4 | Separate explicit CLI registry; in-memory counterfactuals; default S3 fills preserved; no UI/scheduler mutation |
| 20: meaningful offline tests | Done | Fresh baseline, S4/S3 regressions, full-suite result above |
| 23–25/27: legacy scanner and frozen setup semantics | Preserved | No screening/ranking changes; S3 default references remain identity |
| 29 / swing S4: preregistration, uncertainty, stress and promotion gates | Tooling done; real validation blocked | Code/ledger/calendar/runtime manifests and explicit insufficient-evidence outcomes |

## STATE SUMMARY

```text
Phase: S4 independent validation tooling, 17 Oct 2026.
Implemented: immutable protocols/search budgets and isolated validation registry.
Implemented: expanding folds, session embargo and holding-horizon signal purge.
Implemented: future fold tests and one-shot future terminal holdout reservations.
Implemented: S3 chain/plan reconciliation in one read snapshot.
Implemented: no training/OOS pooling or cross-window account carry.
Implemented: net INR/R expectancy, distributions, capacity/fill/NAV coverage.
Implemented: opening/closing exposure, portfolio drawdown and underwater duration.
Implemented: gross index, cash and net fixed-exposure baseline comparisons.
Implemented: seeded signal-date moving-block uncertainty and sparse-sample gates.
Implemented: bounded distinct parameter neighbors and adverse cost sensitivity.
Implemented: isolated delay/gap/halt portfolio replay, no observed bar changes.
Implemented: extra-path missingness stays unresolved; no hypothetical late fills.
Implemented: protocol/code/calendar/runtime/input hashes and optional HMAC.
Implemented: schema/register/development/holdout/show/verify/audit CLI.
Current real status: RESEARCH_ONLY; no statistical validation or promotion.
Actual source audit: 497 constituents, 241 with prices, 0 complete histories.
Blockers: PIT membership, publication, adjustment/identity, execution context.
Blockers: Nifty 500 benchmark absent, incomplete histories.
Actual manifests: UNSIGNED; no real signing key supplied/generated.
Actual observation-store SHA unchanged; paper/validation stores remain absent.
Real accounts/studies/holdouts/fills created: none.
Synthetic verification lives only in test fixtures; no synthetic API/UI path.
Baseline: 356 passed, 1 existing warning.
New S4 cases: 38; targeted S4 run passed.
Final full suite: 394 passed, 1 existing warning, 165.82 s.
Preserved: legacy scanner, existing S1–S3 dirty work and user root redirect.
No commit/push, network/source edit, account funding or broker integration.
S5 UI/operations and S6 prospective observation remain pending.
Stop after S4; a later phase requires explicit user authorization.
```

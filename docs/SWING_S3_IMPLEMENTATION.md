# Swing S3 implementation report

Completed 16 October 2026: durable research accounts/plans, reservations,
versioned commands, conservative replay, event reconstruction and a shared-lock
job queue. S4–S6 have not started. No broker orders, validated-paper promotion,
real account funding or automatic capital deployment were enabled.

## Delivered behavior

- Separate append-only SQLite ledger. All financial changes, plan capture and
  command receipts commit atomically under BEGIN IMMEDIATE. Explicit funding is
  required; startup and reads cannot invent an account or balance.
- Immutable original plans and logical setup deduplication. A refresh cannot
  overwrite the initial stop/target, duplicate a reservation or silently replace
  its consumed-data revision. Hash-chained events reconstruct accounts after
  restart; consistent backup and unrelated/future-schema refusal are tested.
- Idempotency across funding, commands, accepted jobs and restart, with optimistic
  account versions. A cancellation cannot steal an accepted replay job's key.
  Reservations may not exceed settled cash; open/unresolved liabilities cannot
  be cancelled as though unfilled.
- Explicit next-open entry bounds, gap rejection, actual frozen costs, no increase
  beyond reserved quantity, per-side capacity, partial entry remainder cancellation
  and partial exits preserving the outstanding liability. Actual reconciled NAV
  is a runtime sizing input; frozen strategy and fee parameters remain intact.
- Stops fill at the adverse open on gaps. Daily bars touching stop and target use
  stop first, with a separate target-first fill sensitivity. Intraday proceeds
  cannot fund purchases at the earlier open. Time exits use the next tradable
  open after the holding-limit close.
- Verified held split/bonus ratios adjust live quantity and price levels while
  preserving original plans and cash basis. Fractional, pending-order action,
  unsupported distribution or ambiguous identity cases stay unresolved.
  Missing bars/context/capacity never become invented fills or guaranteed exits.
- Explicit unresolved paths, stale/unavailable NAV, frozen event history and
  gross Nifty 500 price-index comparison. A missing initial benchmark open cannot
  be replaced with a later favorable starting point. Outcomes with uncertain
  held paths are excluded from resolved P/L summaries.
- Durable jobs are saved before HTTP 202, then drained by the existing scheduler
  under its global scan lock. Work stays off-loop and rechecks scan priority after
  preflight. Failed jobs roll back financial changes and retain an error receipt;
  unfinished jobs remain discoverable after restart. No second periodic loop.
- Opt-in research APIs for funding, queueing, account/plan/event/job/performance
  reads and cancellation of known unfilled orders. HTTP accepts no market bars,
  fill prices/quantities, evidence attestations or model promotions. Disabling
  swing/replay preserves reads and liabilities.

All accounts are `RESEARCH_REPLAY`; model status stays `RESEARCH_ONLY` and
`paper_eligible=false`. ARMED/OPEN are simulation lifecycle states. The complete
frozen timeline, inequalities, costs, unsupported-data policies and endpoints are
in [SWING_S3_CONTRACT.md](SWING_S3_CONTRACT.md).

## Files created or modified in S3

| Files | Purpose |
|---|---|
| `app/swing/ledger.py` | Append-only accounts/plans/jobs/events/receipts, reconstruction, cash invariants and backup |
| `app/swing/execution.py` | Typed execution-evidence protocol and conservative read-only S1 adapter |
| `app/swing/replay.py` | Session lifecycle, frozen costs, bounded fills/actions, marks and unresolved paths |
| `app/swing/paper_service.py` | Queue/capture/replay orchestration, cancellations and descriptive results |
| `app/swing/paper_api.py` | Validated opt-in commands and account/plan/event/job/performance reads |
| `app/swing/risk.py` | Explicit dynamic-NAV seam; default S2 behavior preserved; held risk includes frozen liabilities |
| `app/swing/config.py`, `app/core/config.py` | Three S3 defaults, bounds and separate-store path validation |
| `app/swing/api.py` | Accurate opt-in replay/worker status |
| `app/main.py` | Wires paper service/router into existing application/scheduler |
| `app/scheduler/runner.py` | Drains queued research work with shared lock and scan priority |
| `tests/fixtures/swing_replay.py` | SYNTHETIC TEST-only execution observations and lifecycle helpers |
| `tests/test_swing_replay.py` | Session/fill/cost/action/NAV/causality and conservation checks |
| `tests/test_swing_ledger.py` | Atomicity, concurrency, corruption, immutable state, funding and key/version tests |
| `tests/test_swing_paper_api.py` | Isolated queue/API/restart/rollback/non-blocking/priority checks |
| `.env.example`, `README.md` | Opt-in defaults and research-only usage |
| `docs/SPEC.md`, `docs/SWING_TRADING_PLAN.md` | S3 scope and updated phase status |
| `docs/SWING_S3_CONTRACT.md`, this report | Frozen rules, evidence and handoff |
| `docs/swing/S3_REAL_CHECK.json` | Actual read-only source/adapter gate and final native hashes |
| `docs/swing/S3_REUSE_MANIFEST.json` | Hashes of two inspected sibling public source files |

S1/S2 and pre-existing working-tree edits remain present. The prior root redirect
and `tests/test_api.py` changes were preserved. No commit/push, dependency change,
source-project database write, legacy-history migration or UI edit was performed.

## Commands executed and test results

Read `AGENTS.md`, the master spec and S1/S2/swing contracts. Used CodeGraph for
the current scheduler/risk/plan/API dependencies and sibling replay/exit/cost
source. The existing Graphify lookup supplied no replay matches; no graph edges
were inferred from that lookup. Current source was inspected with CodeGraph.
Pinned the two inspected source hashes in the S3 reuse manifest; no credentials,
source schemas or source-project account records were copied.

Executed `git status --short`, `git diff --check`, targeted `git diff` for
application/scheduler integration, and compilation:

```text
.venv/Scripts/python.exe -m compileall -q app/swing app/scheduler/runner.py tests/test_swing_replay.py tests/test_swing_ledger.py tests/test_swing_paper_api.py
```

Baseline and full suites used fresh workspace temporary directories. Exact final
PowerShell command:

```powershell
$taskTmp=Join-Path (Get-Location).Path ('logs/swing-s3-verified-'+[guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe -u -m pytest -q -p no:cacheprovider --basetemp $taskTmp --tb=short
```

Focused runs selected `tests/test_swing_replay.py`, then also
`tests/test_swing_ledger.py`, `tests/test_swing_paper_api.py` and
`tests/test_swing_risk.py`, with the same isolated pytest options.

Actual successful output:

```text
Fresh baseline: 311 passed, 1 warning in 127.54s (0:02:07)
First lifecycle suite: 17 passed in 10.43s
First complete S3 suite: 350 passed, 1 warning in 157.40s (0:02:37)
Expanded complete suite: 354 passed, 1 warning in 148.70s (0:02:28)
Final complete suite: 356 passed, 1 warning in 151.43s (0:02:31)
```

The remaining warning is the pre-existing Starlette/AnyIO BlockingPortal
deprecation. An intermediate focused run caught zero-P/L formatting (`0` versus
`0.00`); it was corrected before successful full runs. Later checks added
same-open multi-position marking, processed-revision immutability, benchmark
start coverage, job-key isolation, cash-reservation invariants and the preflight
scan-priority race. Final source has 45 added S3 cases. Compilation and whitespace
checks exited 0; Git prints existing Windows LF/CRLF conversion notices.

## Actual real-data check

Executed a direct read-only S2 evaluation and `ArchiveReplayMarket.observe` using
`SystemClock`, the existing real S1 vintage and actual public cached coverage.
Compared the observation DB SHA256 before/after and checked that the paper DB
does not exist. No seed capital was supplied, no account was initialized, and no
job or fill was created. The check was repeated against final native source;
details/hashes are in [S3_REAL_CHECK.json](swing/S3_REAL_CHECK.json).

```text
Vintage: 54e87b147932a02732f6931adf6fb8067c9a4f2d2084126e816f1d94a97d10aa
Universe: 497; plans: 0; failed symbols: 497
Representative actual cached observation: AARTIIND.NS, 2026-10-15
Execution bar unavailable: price_basis_unresolved_or_mismatched
Actions verified: false; tradability/buy capacity/sell capacity: unknown
Observation database bytes unchanged: true
Paper database/account created: false; jobs/fills created: 0
```

This verifies gate behavior, not source correctness or a tradable signal set.
Existing missing Nifty 500/basis/identity/publication evidence remains unresolved.
S1's cached rows cannot attest execution capacity or corporate actions.

## Assumptions and verification boundaries

Assumptions: local single-user NSE long-only unlevered research, completed daily
bars and an injected calendar/clock. Explicit funding is simulated capital,
not a connected broker account. Execution requires typed, dated, raw-basis and
capacity/action-verified inputs. Costs are the existing configurable estimates,
frozen in plans; Decimal paise rounding is a research accounting convention,
not a statutory contract-note calculation. Original plans are preserved when
known actions transform a held position.

VERIFIED: atomic financial events/receipts, no duplicate execution after restart
or concurrent retries, account/key/version isolation, cash/quantity conservation,
reserved-cash bounds, per-side partial fills, adverse gaps/ambiguous candles,
time exits, known held splits, unsupported-path blocking, immutable original
plans and processed NAV, backup/corruption refusal, queued recovery, API rollback
reads, worker non-blocking behavior and scan priority; final full regression
suite and actual read-only source gates.

NOT VERIFIED: real fills or actionable swing ideas, live capacity/tradability
feeds, real raw/action/identity reconciliation, special-session completeness,
statutory fees/tick precision, economic dividend/fractional entitlement handling,
broker reconciliation, large-history throughput, profitable strategy behavior or
held-out/prospective validation. Unsupported/unresolved historical paths require
reviewed reconciliation and are not silently repaired by a new vintage or
unfilled cancellation. The real-data adapter cannot resolve them today. Event
reconstruction is deliberate; large-history projection optimization is not
claimed. No running user browser/server was restarted or visually verified.

## SPEC COMPLIANCE

| Section | Status | Evidence |
|---|---|---|
| 0/22 — phase discipline/report | Done | S3 only, real checks/results and handoff |
| 1/6/10–12 — legacy screening | Done | Unchanged funnel/ranking; legacy suite passes |
| 2/3 — integrity/stack | Done | No fabricated real data, credentials or new dependencies |
| 5 — configuration | Done | Three opt-in/bounded S3 settings and path separation |
| 7–9/16 — data/time | Done | Explicit evidence protocol, completed sessions, frozen plans and unknown states |
| 14 — persistence | Done | Separate append-only transactional ledger, receipts, jobs and backup |
| 15 — scheduling | Done | Existing global lock/worker, durable manual queue and priority recheck |
| 17 — API | Done | Validated models, idempotency/version conflicts, rollback reads and no fill injection |
| 18 — UI | Not in this phase | S5 remains pending |
| 20 — tests | Done | 356 passing tests; 45 new S3 cases |
| 23–27 — prior features/S1/S2 | Done | Preserved contracts, costs and data gates; default S2 allocation remains compatible |
| 28 — S3 | Done | Research lifecycle, conservative replay, account/NAV/benchmark evidence and queue |
| S4–S6 — validation/UI/forward paper | Not in this phase | No promotion or prospective-validation claim |

## STATE SUMMARY

```text
Completed: S3 research replay/lifecycle, 16 Oct 2026; S4–S6 not started.
app/swing/ledger.py: PaperLedger(path,clock,timeout); create_account/command/capture,
  state/plan/events/enqueue/job/next_job/backup; Decimal money and hash-chain reducer.
app/swing/execution.py: ExecutionObservation; ReplayMarket.observe/returns protocol;
  ArchiveReplayMarket is read-only and does not invent execution attestations.
app/swing/replay.py: ReplayEngine(...).advance(conn,state,emit,vintage,through).
app/swing/paper_service.py: PaperService(...).create_account/enqueue/cancel/process/performance.
app/swing/paper_api.py: explicit funding/replay/cancel; account/plan/event/job/result reads.
app/swing/risk.py: opt-in dynamic_nav seam; frozen strategy/costs and default S2 intact.
app/swing/config.py + app/core/config.py: S3 defaults/bounds and store-path guard.
app/swing/api.py: accurate replay-enabled and worker-running status.
app/main.py: attaches paper service/router to existing scheduler.
app/scheduler/runner.py: evaluate_swing_if_idle(), shared lock and preflight priority.
tests/fixtures/swing_replay.py: SYNTHETIC TEST-only execution provider/helpers.
tests/test_swing_replay.py: causal execution, cash/quantity/actions/NAV/expiry tests.
tests/test_swing_ledger.py: durable/immutable/concurrent/corruption/cancellation checks.
tests/test_swing_paper_api.py: queue restart, rollback reads, schema and worker races.
.env.example + README.md: opt-in settings and API behavior.
docs/SPEC.md section 28 + docs/SWING_TRADING_PLAN.md: S3 scope/status.
docs/SWING_S3_CONTRACT.md + docs/SWING_S3_IMPLEMENTATION.md: rules/evidence/handoff.
docs/swing/S3_REAL_CHECK.json: actual gates/final native hashes; no account created.
docs/swing/S3_REUSE_MANIFEST.json: hashes of two inspected sibling public code files.
New keys: SWING_PAPER_DB_PATH=data/swing_paper.db; SWING_REPLAY_ENABLED=false;
  SWING_REPLAY_MAX_SESSIONS=250 (1..5000). Existing frozen fees/limits reused.
Decisions: RESEARCH_REPLAY accounts; model remains RESEARCH_ONLY/paper_eligible=false.
Decisions: explicit funding, immutable plans/events/receipts, versioned idempotent jobs.
Decisions: reserve cash; next-open bounds; stop first; adverse gaps; partial exit liability.
Decisions: no guessed actions/dividends/capacity, forward-filled NAV or historical repairs.
Known unresolved entry retains funds; open/unresolved orders cannot be cancelled as unfilled.
No automatic account, second periodic loop, external fill injection or broker integration.
Real check: 497 symbols, zero plans/fills; source DB unchanged; no paper DB created.
Tests: baseline 311; final 356 passed, one existing warning; compile/diff checks exited 0.
Preserved S1/S2 and prior root redirect/test_api.py edits; no commit/push.
Known limits: real source/execution evidence, corporate entitlements, edge and scale unverified.
Next after explicit CONTINUE: reread SPEC/S1–S3; freeze S4 untouched holdout, walk-forward,
  uncertainty/stress/promotion protocol before opening evaluation data; audit data gaps first.
Stopped after S3. No S4 validation, S5 UI or S6 prospective paper claim.
```

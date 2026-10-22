# S6 — Forward paper reconciliation

Implemented S6 **tooling**, 17 Oct 2026. Real prospective observation is **unstarted**:
no capital/window was invented, no campaign registered and no strategy promoted.
The current archive still fails required source gates.

## Delivered behavior

Future campaign registration freezes an unused explicitly funded S3 account,
starting event head, complete session schedule, hypothesis, strategy configuration,
code/runtime fingerprints, minimum session count and wind-down boundary. A
registration cannot be backdated or retrofitted onto an already traded account.
The terminal tail covers the maximum holding horizon, entry validity and next-open
exit; new plans in that tail are a protocol violation. The operator uses S5
management-only replay to retain/exit existing holdings without new entries.

After each actual close and completed S3 replay, an explicit capture seals the
chosen verified source vintage, new plans/events, observations, account state and
reconciliation failures before the next open. Empty days must also be captured.
Retries keep their original receipt. Missing days, replaced vintages and late
plans cannot silently become timely evidence. Reads create no stores; capture
does not change source data, paper balances, fills, orders or account capital.

Reconciliation checks the prior sealed reservation/entry interval against the
subsequent raw open, identity/actions, explicit capacity and recorded simulated
fill. It independently checks reference prices, quantity bounds, estimated costs,
gross/cash arithmetic, exit triggers, observed close marks, NAV and initial index
open. Original ledger observations versus later observations expose revisions.
Only active/held/event securities require execution observations, so a closed
position does not require perpetual future prices. Missing source/context/marks
remain unresolved; entry gaps remain ordinary non-fills. S3 hash chains and plan
hashes are checked in a consistent read transaction before consuming events.

The separate append-only journal uses schema validation, chained content hashes,
idempotent receipts and protected paths. It detects content/sequence changes;
it is a local audit trail, not an independent auditor's authenticated signature.
Full account snapshots are retained daily for inspection along with incremental
events/new plans. This favors auditability over long-horizon storage efficiency.

HTTP/UI provide read-only monitoring under Trade Ideas → Validation, with captured
versus reconciled sessions, missing dates, failed gates and planned/observed
comparisons. Synthetic campaigns are excluded from discovery and direct-ID reads.
No HTTP mutation, hidden timer, provider fetch, automatic replay or promotion was
added. The CLI is the explicit daily recording workflow; an operator must run it.

Every report retains `RESEARCH_ONLY`, `paper_eligible=false` and
`promotion_allowed=false`. Completing >=30 actual sessions with a reconciled flat
final book only satisfies an operational gate. Independent S4 samples, uncertainty,
parameter/cost/execution stresses and untouched holdout requirements remain separate.
Daily OHLC references and simulated fills never claim broker confirmations.

## Files created / modified

Created:

- `app/swing/forward_store.py`: settings, immutable campaigns/days/receipts.
- `app/swing/forward.py`: prospective registration, observation and reconciliation.
- `app/swing/forward_api.py`: read-only campaign/report routes.
- `scripts/swing_forward.py`: explicit register/capture/report/verify/list CLI.
- `tests/fixtures/swing_forward.py`, `tests/test_swing_forward.py`: labelled
  SYNTHETIC fixtures and 23 focused cases.
- `docs/SWING_S6_CONTRACT.md`, this report, [real check](swing/S6_REAL_CHECK.json)
  and [browser screenshot](swing/S6-desktop.jpg).

Modified: `app/main.py` (dependency/router wiring), `ui/swing.py` (monitor),
`.env.example`, `README.md`, `docs/SPEC.md` §31, `docs/SWING_TRADING_PLAN.md`,
`tests/test_swing_operations.py` (safe API checks), `tests/test_swing_ui.py`
(empty/populated monitor checks) and `tests/fixtures/swing_ui.py` (test response).
Ignored local helper: `logs/swing_s6_real_check.py`. Existing S1–S5/user dirty work
was preserved. No dependencies, `.env` changes, commits or pushes.

Public entry points: `ForwardSettings`, `ForwardJournal(settings, clock,
protected=(), timeout=30)`, `ForwardService(config, clock, calendar, settings=None,
paper=None, protected=())`, `register(campaign, account_id, start, end, hypothesis,
key)`, `capture(campaign, vintage, key)`, `report(campaign)`, `reconcile(...)`,
`ui.swing.forward_view(client)`. New defaults:
`SWING_FORWARD_DB_PATH=data/swing_forward.db`, `SWING_FORWARD_MIN_SESSIONS=30`
(validated 30..5000; no shortening below 30).

## Commands and executed verification

Read the complete SPEC (including a second bounded read of the initially truncated
middle), AGENTS, S3/S4/S5 contracts and swing plan; queried CodeGraph integration
paths before edits. Ran `git status --short`, `git diff --check`, Python syntax,
environment-default/hash/reference checks and:

| Command | Actual result |
|---|---|
| `.venv/Scripts/python.exe -u -m pytest tests/test_swing_forward.py tests/test_swing_ui.py -q ...` initial | 11 failed, 15 passed; fixed Pydantic observation serialization |
| `pytest tests/test_swing_forward.py -q ...` after fix | 18 passed, 1 warning, 8.96 s |
| `pytest -q ...` first completed regression | 435 passed, 1 warning, 187.35 s |
| `pytest -q ...` including wind-down/API/populated monitor checks | **438 passed, 1 warning, 188.39 s** |
| `pytest tests/test_swing_forward.py -q ...` after final index-open/active-symbol tightening | **23 passed, 1 warning, 13.74 s** |
| `python scripts/swing_forward.py --help` | CLI commands shown, exit 0 |
| `python scripts/swing_forward.py list` | `[]`, exit 0; no store created |
| `python scripts/swing_forward.py verify unregistered` | BLOCKED: No forward campaigns registered, expected exit 1 |
| `python -u logs/swing_s6_real_check.py` | Real stores unchanged; zero forward sessions; promotion false |

All pytest invocations used the repository venv, `-p no:cacheprovider`, `--tb=short`
and unique workspace `--basetemp` plus TEMP/TMP directories. Initial baseline and
two sandbox repetitions were interrupted when Windows asyncio blocked creating
its local socket pair; faulthandler confirmed that environmental cause. They are
not reported as passed. Completed runs used reviewed local access with market
network requests mocked. The preceding S5 414-pass result is historical, not a
fresh S6 baseline. The 438-case run preceded the final small reconciliation change;
the subsequent 23-case focused run exercised that final code and its added test.

Actual full-suite output:

```text
438 passed, 1 warning in 188.39s (0:03:08)
```

Final reconciliation output:

```text
23 passed, 1 warning in 13.74s
```

The warning is the existing Starlette TestClient BlockingPortal deprecation.
Tests cover future-window/minimum/isolation/schema rules, retries, read safety,
timely forecast/open matching, gap non-fills, unknown capacity, cash/reference
discrepancies, revisions, missed sessions, event-head drift, append-only/tamper,
configuration drift, wind-down and synthetic exclusion. Thirty *simulated test*
sessions remain synthetic and cannot qualify as real validation.

Browser verification used the actual loopback preview at 1280×850, selected the
Validation tab, checked the unregistered/blocked state and saved the screenshot.
Viewport was restored. The preview uses the existing scan-free helper:

```text
python -u -m uvicorn logs.swing_s5_preview:app --host 127.0.0.1 --port 8000 --log-level warning
python -u -m streamlit run ui/app.py --server.port 8501 --server.address 127.0.0.1 --server.headless true
```

Scheduler, replay worker, nightly outcomes and external alerts are disabled; all
preview mutations return 403. No unrelated NYSE scans were enabled.

## Real evidence and remaining prerequisites

The [final audit](swing/S6_REAL_CHECK.json) records 497 current constituents,
241 symbols with prices, zero complete trailing histories and six source blockers:
point-in-time membership, historical publication, adjustment/identity, execution
context, missing Nifty 500 benchmark and incomplete histories. The current S1
adapter cannot attest those facts; richer verified evidence is still required.
No synthetic data was exposed to the real API/UI to fill those gaps.

Real observation-store SHA256 remains
`506fff77cb8127dba8a3e075da0417bd3d7b5b57a6f7f149494f109476d3d57e`.
Paper, operations, validation and forward journal stores remain absent. Actual
forward sessions, registered campaigns, accounts created, real fills, external
deliveries and model promotions this phase: **zero**. Existing S4/S5 artifacts
retain their historical fingerprints and were not rewritten.

Assumptions: completed NSE cash-delivery sessions; explicit research capital and
future dates; frozen S2/S3 cost/fill policies; user-run daily capture. XBOM special
sessions, licensed/live provider access, actual opening capacity, real-world fills,
adequate statistical samples and future edge remain unverified. Relative paths
keep the existing configured working-directory semantics. Source repairs and a
user-specified funded account/window are prerequisites to start a useful real run.

## VERIFIED vs NOT VERIFIED

| VERIFIED by executed checks | NOT VERIFIED / blocked |
|---|---|
| Future registration, sealed daily evidence, exact-session windows and missed dates | Any actual prospective session window; zero recorded |
| Reference/cash/quantity/NAV/index-base reconciliation and revision failures | Broker fills, real tradability/capacity and market performance |
| Immutable journal/receipts, source/ledger isolation, read-only API/UI | Independent external journal authentication |
| Synthetic test paths cannot pass as real evidence | Sufficient real regimes/trades or statistical edge |
| Real stores unchanged; unregistered browser/API/CLI states | Strategy promotion; independent S4 gates remain blocked |

## SPEC COMPLIANCE

| Section / invariant | Status | Evidence |
|---|---|---|
| 0/21/22: one phase, spec/report/checks | Done for S6 tooling | Scope, real outputs and handoff here |
| 2/6/26: no fabrication; explicit source evidence | Done; real source gate blocked | Synthetic fixtures only; actual store hashes unchanged |
| 3/5/7/8: providers/config/Clock | Done | Existing observation protocol, injected Clock, environment defaults; no dependencies/network fetching |
| 14/15/17/18: storage/nonblocking/API/UI | Done | Isolated journal, read-only off-loop routes and HTTP-only monitor; no new scheduler |
| 20: regression checks | Done | Completed full and final focused results above |
| 27–30: frozen strategies/replay/validation/operations | Preserved | No S2/S3 financial changes, holdout opening, automatic enable or artifact rewriting |
| 31: forward paper reconciliation | Partial | Tooling verified; actual >=30-session observation and real evidence still outstanding |

## STATE SUMMARY

```text
Phase: S6 forward paper reconciliation tooling, 17 Oct 2026.
Created app/swing/forward_store.py: settings and immutable journal/receipts.
Created app/swing/forward.py: registration, capture, reconciliation and reports.
Created app/swing/forward_api.py: read-only campaign/report monitoring.
Created scripts/swing_forward.py: register/capture/report/verify/list CLI.
Created tests/fixtures/swing_forward.py and tests/test_swing_forward.py.
Modified app/main.py: service/router injection; no trading behavior changes.
Modified ui/swing.py: Validation forward monitor, no backend imports.
Modified tests/test_swing_operations.py, test_swing_ui.py, fixtures/swing_ui.py.
Modified .env.example, README, SPEC section31 and SWING_TRADING_PLAN.
Created S6 contract/report/real audit/browser screenshot.
Public: ForwardSettings; ForwardJournal(settings,clock,protected=(),timeout=30).
Public: ForwardService(config,clock,calendar,settings=None,paper=None,protected=()).
Public: register(campaign,account,start,end,hypothesis,key); capture(campaign,vintage,key).
Public: report(campaign), reconcile(...); HTTP GET forward/campaigns[/id].
Config: SWING_FORWARD_DB_PATH=data/swing_forward.db.
Config: SWING_FORWARD_MIN_SESSIONS=30; cannot be configured below 30.
Decisions: future preregistration, canonical daily seal, no historical backfill.
Decisions: frozen wind-down; explicit S5 management-only replay in terminal tail.
Decisions: observed references vs simulated fills; no broker confirmations.
Decisions: CLI writes only; no new timer, auto-account, download or promotion.
Full regression: 438 passed, 1 existing warning, 188.39 s.
Final focused reconciliation after last tightening: 23 passed, 1 warning, 13.74 s.
Synthetic 30-session test never counts as real validation.
Real: 497 constituents, 241 priced symbols, zero complete histories, six blockers.
Actual campaigns/forward sessions/accounts/fills/promotions/outbound alerts: zero.
Observation store unchanged; real paper/ops/validation/forward stores absent.
Existing dirty user/S1-S5 work and historical manifests preserved; no commit/push.
Known: S1 cannot attest PIT/publication/basis/action/capacity; benchmark missing.
Next: resolve verified source evidence, obtain explicit account capital/window,
then preregister before first open and capture/reconcile each actual session.
S6 real operational/statistical validation is not complete; stop after tooling.
```

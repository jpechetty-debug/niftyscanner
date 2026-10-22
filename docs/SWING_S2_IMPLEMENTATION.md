# Swing S2 implementation report

Completed 16 October 2026: two independent daily swing hypotheses, immutable
research calculations, cost-aware entry/risk levels, portfolio allocation
preview and an opt-in read-only API. S3–S6 have not started.

## Delivered behavior

- `DAILY_BREAKOUT_V1`: rising daily trend and Nifty 500 regime, positive relative
  strength, completed prior-high breakout and volume confirmation.
- `DAILY_PULLBACK_V1`: controlled retracement in an established trend, quiet
  preceding volume and completed-bar reclaim. It does not pass through the
  legacy RSI/volume/P/E funnel.
- Explicit EMA/ATR recurrences, 400-session default warm-up, exact calendar
  alignment, previous-session liquidity baselines and 21/63-session excess RS.
- Structural stop, mechanical/capped target, net RR after existing configurable
  NSE cost assumptions, bounded entry interval, signal-anchored entry expiry and
  holding-limit exit instruction. Refreshing after the entry window expires
  cannot extend that window. Continuous reference prices are not tick-rounded orders.
- Frozen configuration/costs, source/evidence/data/implementation hashes,
  decision/session/identity/basis, explanations, passed/missing gates and
  requested risk. HTTP quantities/cash requirements remain null without a
  reconciled account integration; no account balance is invented.
- Pure allocation preview validates marked NAV/cash, held and pending books,
  frozen reservation costs, portfolio heat/gross/sector/correlation/ADV limits,
  duplicate securities, observed-open gaps and net RR. A positive quantity
  remains hypothetical; it cannot arm a plan. No book or stop is mutated.
- `GET /api/swing/status`, `/api/swing/results?vintage=<sha256>` and
  `/api/swing/data-quality?vintage=<sha256>`. Data reads require the opt-in flag
  and NSE; no import, network request, schedule or write is triggered.
  SQLite, hashing and evaluation run in a worker thread.

All plans remain `RESEARCH_ONLY` / `CANDIDATE`, with `paper_eligible=false` and no
validation record. Durable plans/orders/reservations/events/replay belong to S3;
promotion belongs to S4 and UI to S5. The frozen definitions, inequalities,
units, exact 44 S2 defaults/bounds and API conventions are in
[SWING_S2_CONTRACT.md](SWING_S2_CONTRACT.md).

## Files created or modified in S2

| Files | Purpose |
|---|---|
| `app/swing/config.py` | 44 validated environment-driven research settings |
| `app/swing/plans.py` | Frozen evidence, features, costs, plans and result types |
| `app/swing/indicators.py` | Explicit EMA and SMA-seeded Wilder ATR |
| `app/swing/setups.py` | Completed-session preparation, independent setup gates and bounded plans |
| `app/swing/risk.py` | Reconciled account/book types and deterministic allocation preview |
| `app/swing/service.py` | Read-only S1 vintage verification and research funnel |
| `app/swing/api.py` | Opt-in research routes, response models and off-loop work |
| `app/core/config.py` | Inherits S2 environment fields; S1/legacy settings preserved |
| `app/main.py` | Registers research router; pre-existing root redirect preserved |
| `tests/fixtures/swing_setups.py` | Labelled SYNTHETIC TEST-only setup histories |
| `tests/test_swing_setups.py` | Independent indicators, causality, sessions, levels and configuration |
| `tests/test_swing_risk.py` | Cash/risk/sector/liquidity/correlation/reservation boundaries |
| `tests/test_swing_api.py` | Isolated read-only/error/schema/event-loop checks |
| `.env.example`, `README.md` | Defaults, API usage and research limitations |
| `docs/SPEC.md`, `docs/SWING_TRADING_PLAN.md` | S2 contract reference and phase status |
| `docs/SWING_S2_CONTRACT.md`, this report | Frozen rules, evidence and handoff |
| `docs/swing/S2_REAL_CHECK.json` | Actual read-only archive-check aggregate/hashes |

S1 files/evidence remain in the existing dirty working tree. The pre-existing
`tests/test_api.py` changes were not edited. No commit or push was performed.
No new dependency, database migration, scheduler hook, UI or broker code was added.

## Commands and real test output

Read `AGENTS.md`, the full master spec, swing plan and S1 report. Used CodeGraph
for current Settings/repository/application contracts and the sibling
`detect_swing_trigger`/`swing_fill_size` implementation before porting rules.
Inspected `git status --short`, `git diff --stat`, `git diff -- app/main.py` and
ran `git diff --check`.

Baseline and final suite used this PowerShell command with a new workspace
temporary directory on each run (Windows sandbox launcher requires the approved
test execution context):

```powershell
$taskTmp=Join-Path (Get-Location).Path ('logs/swing-s2-release-'+[guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe -u -m pytest -q -p no:cacheprovider --basetemp $taskTmp --tb=short
```

Executed focused commands during implementation:

```text
.venv/Scripts/python.exe -u -m pytest -q tests/test_swing_setups.py -p no:cacheprovider --tb=short
.venv/Scripts/python.exe -u -m pytest -q tests/test_swing_setups.py tests/test_swing_risk.py -p no:cacheprovider --tb=short
.venv/Scripts/python.exe -u -m pytest -q tests/test_swing_setups.py tests/test_swing_risk.py tests/test_swing_api.py -p no:cacheprovider --basetemp <workspace temporary directory> --tb=short
.venv/Scripts/python.exe -m compileall -q app/swing tests/test_swing_setups.py tests/test_swing_risk.py tests/test_swing_api.py
git diff --check
```

Actual successful output:

```text
Baseline: 261 passed, 1 warning in 101.13s (0:01:41)
Intermediate setup/risk suite: 42 passed in 18.10s
First complete S2 suite: 311 passed, 1 warning in 125.73s (0:02:05)
Final complete suite after expiry/evidence fixes:
311 passed, 1 warning in 125.03s (0:02:05)
```

The remaining warning is the existing Starlette/AnyIO `BlockingPortal`
deprecation. Development failures were corrected before the final run: calendar
ISO-date normalization, a synthetic pullback fixture whose net RR correctly
failed after costs, and use of the fake clock's public setter. Rules were not
relaxed to make the pullback fixture pass. Final review anchored expiry to the
original signal window. Compilation and whitespace checks exited 0; Git printed
existing Windows LF/CRLF conversion notices. All 44 S2 settings were checked
against `.env.example`.

## Actual real-data check

Executed `SwingResearchService.results(vintage)` directly with `SystemClock`, a
read-only `SwingRepository` and a temporary in-process enabled Settings instance.
This did not change `.env` or enable a running scheduler. Compared SHA256 of the
database file before/after. Recorded aggregate JSON in
`logs/swing-s2/real-data-check.json` and the tracked
[S2_REAL_CHECK.json](swing/S2_REAL_CHECK.json).

```text
Vintage: 54e87b147932a02732f6931adf6fb8067c9a4f2d2084126e816f1d94a97d10aa
Decision: 2026-10-16; required completed session: 2026-10-16
Universe: 497; plans: 0; failed symbols: 497; filtered rules: 0
No exact vintage prices: 256 symbols
Unresolved/mismatched price basis: 241 symbols
Nifty 500 benchmark unavailable: 497 symbols
Evaluation: 0.524 seconds
Database file bytes unchanged: true
```

Failure reasons can overlap; their counts are not additional symbols. Missing
benchmark, unverified historical identity/publication, unresolved action basis
and strategy validation are visible blockers. No Nifty 50 substitution,
fabricated bars, inferred delivery, invented sector or success probability was
introduced. S1 coverage describes the source, not a currently usable trade set.

## Assumptions and verification boundaries

Assumptions: NSE long-only unlevered cash research, completed daily observations,
known typed provider inputs, current supplied universe, simple close-return RS,
continuous research price levels, estimated percentage costs and next-open entry
inside the frozen interval. SWING_CAPITAL_INR is explicit current marked NAV for
the preview and must agree with the reconciled account. Current holdings and
pending reservations must be supplied; S2 does not infer them from scanner picks.

VERIFIED: both hypotheses on isolated synthetic fixtures; independent numerical
references; prefix causality; exact sessions/holiday expiry; net RR/gaps;
cash/risk/exposure/sector/ADV caps; pending/duplicate/correlation behavior;
immutable calculations; research API schemas/errors/read-only behavior and event
loop responsiveness; final complete regression suite; actual real-store blockers
and byte comparison. Legacy scanner tests remain compatible.

NOT VERIFIED: tradable real swing ideas, row-linked adjustment/raw conversion,
point-in-time identity/constituents/context, external live feeds, special-session
calendar completeness, tick rounding/actual fills, durable account/lifecycle,
net strategy profitability or held-out/prospective validation. API tests use an
isolated test application; the user's running browser/server was not restarted
or visually verified. Source schemas and test results are not institutional
certification. Fixed DP fees, statutory rounding, impact and adverse stop gaps
remain limitations of the estimate; no cost accuracy guarantee is made.

## SPEC COMPLIANCE

| Section | Status | Evidence |
|---|---|---|
| 0/22 — one phase/report | Done | S2 only; executed checks and handoff recorded |
| 1/6/10/11/12 — legacy funnel | Done | Separate swing pipeline; filtered/data-failed distinction; legacy tests pass |
| 2/3 — integrity/stack | Done | No fabricated real data or added dependencies; fixtures isolated |
| 5 — config | Done | 44 inherited environment fields, validation and documented defaults |
| 7/8/9/16 — providers/time/indicators | Done | S1 provider seam, explicit vintage/as-of, exact completed sessions and seeded indicators |
| 14 — persistence | Not in this phase | Read-only S1 store; frozen in-memory calculations; durable plans are S3 |
| 15/17 — scheduling/API | Done | Off-loop read-only opt-in routes; no new schedule |
| 18 — UI | Not in this phase | S5 owns the HTTP-only UI |
| 20 — tests | Done | 311 final passing tests; 50 added S2 cases |
| 23–26 — prior extensions/S1 | Done | Existing contracts/evidence preserved; missing data gates remain closed |
| 27 — S2 | Done | Frozen hypotheses, bounded levels, allocation preview and research API |

## STATE SUMMARY

```text
Completed: S2 swing setups/risk plans, 16 Oct 2026; S3–S6 not started.
app/swing/config.py: SwingSettings; 44 inherited environment fields and validation.
app/swing/plans.py: frozen Evidence/Features/Costs/Plan/Evaluation/Rejection; priority(plan).
app/swing/indicators.py: ema(values,period), atr(high,low,close,period).
app/swing/setups.py: evaluate(ticker,rows,benchmark,evidence,config,calendar,decision_at);
  prepare_history(...), required_sessions(cfg), costs(cfg), payoff(...), hashes/configuration.
app/swing/risk.py: Account/BookItem/ReturnPoint/Allocation; correlation_ok(...);
  allocate(plans,config,account,returns,expected_sessions,observed_opens=None,calendar=...).
app/swing/service.py: SwingResearchService(repo,config,clock,calendar).results/data_quality.
app/swing/api.py: /api/swing/status, /results?vintage=sha, /data-quality?vintage=sha.
app/core/config.py: inherits SwingSettings; app/main.py: registers research router.
tests/fixtures/swing_setups.py: labelled SYNTHETIC TEST-only histories.
tests/test_swing_setups.py: causal indicators/setups/levels/config/session tests.
tests/test_swing_risk.py: cash/risk/portfolio/pending/gap/correlation tests.
tests/test_swing_api.py: isolated read-only/schema/error/event-loop tests.
.env.example + README.md: defaults/usage; docs/SPEC.md: section 27.
docs/SWING_TRADING_PLAN.md: phase status; docs/SWING_S2_CONTRACT.md: frozen rules/defaults.
docs/SWING_S2_IMPLEMENTATION.md: this evidence; docs/swing/S2_REAL_CHECK.json: real aggregate.
New key prefix SWING_: ENABLED, MODE, CAPITAL_INR, EMA_FAST, EMA_MID, EMA_SLOW,
  ATR_PERIOD, SLOPE_SESSIONS, RS_LOOKBACKS, VOLUME_LOOKBACK, MIN_AVG_TRADED_VALUE_INR,
  BREAKOUT_LOOKBACK, BREAKOUT_RVOL, BREAKOUT_EXTENSION_ATR, PULLBACK_LOOKBACK,
  PULLBACK_TOUCH_ATR, PULLBACK_DEPTH_ATR, PULLBACK_MAX_RVOL, PULLBACK_RECLAIM_RVOL,
  MAX_EXTENSION_ATR, MAX_CANDLE_ATR, MIN_CLOSE_LOCATION, STOP_BUFFER_ATR,
  MIN_STOP_ATR, MAX_STOP_ATR, TARGET_R, OVERHEAD_LOOKBACK, OVERHEAD_BUFFER_ATR,
  ENTRY_DIP_ATR, ENTRY_STOP_CLEARANCE_ATR, MAX_GAP_ATR, MIN_NET_RR,
  ENTRY_VALID_SESSIONS, BREAKOUT_MAX_HOLD, PULLBACK_MAX_HOLD, RISK_FRACTION,
  MAX_POSITION_FRACTION, MAX_GROSS_FRACTION, MAX_OPEN_RISK_FRACTION,
  MAX_SECTOR_FRACTION, MAX_ADV_PARTICIPATION, MAX_CORRELATION,
  CORRELATION_SESSIONS, MIN_CORRELATION_OBSERVATIONS. Existing cost rates reused.
Decisions: research only; no probability, broker orders, schedule or account invention.
Decision: independent daily hypotheses; preserve separate four-hour and legacy contracts.
Decision: signal-anchored expiry; cost-aware bounds; sizing is hypothetical and read-only.
Real check: 497 symbols, zero plans, benchmark/basis blockers; database bytes unchanged.
Tests: baseline 261; final 311 passed, one existing dependency warning; compile/diff checks 0.
Known limits: real basis/benchmark/identity/context, fills and statistical edge unverified.
Existing main.py root redirect/test_api.py and S1 working changes preserved; no commit/push.
Next after explicit CONTINUE: reread SPEC/S1/S2 contracts and freeze S3 durable lifecycle,
  idempotent reservations/orders/events and conservative replay/fill/cash-conservation rules.
Stopped after S2. No S3 implementation or paper promotion performed.
```

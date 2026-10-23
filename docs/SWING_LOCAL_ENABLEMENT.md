# Local Swing research and replay opt-in

Phase completed on 23 October 2026. The user's follow-up about disabled Swing
research and replay was interpreted as an instruction to enable the existing
features locally. This is a configuration change; strategy and execution code
were not changed.

## Result

The ignored local `.env` now sets:

```dotenv
SWING_ENABLED=true
SWING_REPLAY_ENABLED=true
```

Effective mode remains `research`; capital is not configured. Code defaults and
`.env.example` remain disabled for a fresh installation. No configuration keys,
public functions, dependencies, schemas or thresholds were added.

The API and UI were stopped before this phase and were not started. Start or
restart the existing local API to load the updated configuration. Enabling the
replay flag makes explicit replay commands available and permits the existing
worker to drain submitted jobs when the scheduler runs. It does not create an
account, fund an account or submit a job.

## Preflight and data readiness

Before changing the flags, effective settings were false/false, with no process
environment override. The paper, operations, validation and forward stores were
absent. There were no existing paper accounts, positions or queued replay jobs
that could resume. Local ports 8000 and 8501 had no listener.

The latest real observation vintage remains:

`98b3a73a42aec99d6a3d54536263221630b6a690510d93a0a799d185824c747a`

The completed-session cutoff was 22 October 2026. Coverage remains 457/497 complete
400-session price histories and 493/497 complete 25-session delivery histories.
The benchmark has 400 observations, but its canonical price basis is unverified.

The enabled workspace correctly returned:

- Zero plans, 497 failed symbols and zero normal rule-filtered symbols.
- `price_basis_unresolved_or_mismatched`: 497 symbols.
- `benchmark_unavailable`: 497 symbols, because the raw-basis request cannot be
  satisfied by the current inspection adapter.
- Global gates: strategy validation, benchmark basis, historical identity and
  historical publication remain unresolved.
- View state `evaluation_unavailable`, replacing `disabled`.
- `paper_eligible=false`, `model_status=RESEARCH_ONLY`.

These are evidence failures, not proof that no stocks meet the strategy. The
current cache adapter does not establish raw basis or historical identity just
because original NSE documents exist. Its evidence-aware replacement remains a
separate phase. Forty stocks also retain older price gaps. No evidence gate was
weakened and no unknown observation was relabelled as verified.

## Verification

Machine-readable evidence is in `docs/swing/SWING_LOCAL_ENABLEMENT_CHECK.json`.
The real verification used the effective local configuration and actual imported
data through HTTP with an in-process ASGI transport. No network download or
listening server was involved.

| Check | Actual result |
|---|---|
| `GET /api/swing/status` | 200; enabled and replay enabled |
| `GET /api/swing/vintages` | 200; all six vintages visible |
| `GET /api/swing/workspace` for latest vintage | 200; actual failures shown |
| `GET /api/swing/data-quality` | 200; coverage unchanged |
| `GET /api/swing/paper/accounts` | 200; empty list |
| Replay worker preflight, no queue | No job; no store created |
| Worker-running status | false; scheduler not started |
| Observation and legacy history database hashes | Unchanged |
| Paper/operations/validation/forward stores and WALs | Remained absent |

Existing offline regression tests were executed with synthetic fixtures in
isolated temporary stores. The test shell explicitly set the two feature flags
to false so tests expecting installation defaults do not inherit the user's new
local opt-in. Enabled fixtures explicitly opt in, including tests that submit and
drain a replay job, recover after restart, preserve scan-lock priority, reject
disabled mutations and preserve the data gates.

Command:

```powershell
$env:SWING_ENABLED='false'
$env:SWING_REPLAY_ENABLED='false'
$env:TEMP=(Join-Path (Get-Location) 'logs/swing-enablement-tests')
$env:TMP=$env:TEMP
.venv/Scripts/python.exe -m pytest tests/test_swing_api.py tests/test_swing_paper_api.py tests/test_swing_operations.py -q -p no:cacheprovider --basetemp logs/swing-enablement-tests/pytest-approved --tb=short -o faulthandler_timeout=60
```

Actual output:

```text
26 passed, 1 warning in 31.33s
```

The warning is Starlette's existing deprecated `anyio.abc.BlockingPortal` alias.
The full suite was not rerun for this local configuration change.

The initial sandboxed check stalled before the first test in Python's Windows
event-loop socket-pair creation. The read-only real check stalled there as well.
Both task-specific processes were stopped and the checks rerun successfully
outside that restriction. One launcher had already exited when its cleanup was
attempted. An initial helper invocation by file path failed to import `app`;
running it as a module from the repository root succeeded. No failed attempt is
counted as a passing test.

Other commands executed:

- `Get-Content docs/SPEC.md` in bounded sections; Graphify skill/query reference.
- `graphify reflect --if-stale`; read lessons and existing graph vocabulary.
- `graphify query 'swing research replay settings enabled operations paper' --dfs --budget 600`.
- CodeGraph exploration of Swing settings, API gates, workspace, replay service,
  paper ledger, operations controls and scheduler preflight.
- Read-only Python inspection of effective flags, store existence and local ports.
- A bounded Python edit of only the two local flag definitions, followed by
  `load_settings()` assertions.
- `.venv/Scripts/python.exe -u -m logs.check_swing_enablement`.
- `git check-ignore .env` returned `.env`.
- `git diff --check` for final whitespace verification.
- Graphify `save-result` to retain this phase's findings.

Graphify expansion used only existing vocabulary:
`[swing, research, replay, settings, enabled, operations, paper]`. Its bounded
traversal supplied navigation; current CodeGraph source and executed checks
established the configuration behavior. No graph rebuild was needed.

## Files created or modified

- `.env` (ignored): explicitly enable the two local feature flags.
- `docs/SPEC.md`: section 40 records this opt-in and its verification boundary.
- `docs/SWING_LOCAL_ENABLEMENT.md`: this report, commands, results and limitations.
- `docs/swing/SWING_LOCAL_ENABLEMENT_CHECK.json`: actual HTTP/coverage/hash evidence.
- `logs/check_swing_enablement.py` (ignored): read-only verification helper.
- Graphify memory/reflections: query feedback and refreshed lessons.

## Assumptions and verification limits

VERIFIED: effective flags true/true; research mode; no configured capital; real
HTTP reads return 200; evidence failures remain explicit; no queued work,
accounts, financial mutations, source changes, alerts or model promotion;
26 targeted tests pass.

Assumption: the user's disabled-feature follow-up requests a local opt-in to
the existing research/replay implementation. It supplies no account identity,
funding amount, replay request or instruction to start the servers.

NOT VERIFIED: a running local API/UI after restart, usable real-data strategy
plans, successful real-data replay, independent validation, prospective paper
performance or a profitable strategy. These are not implied by enabled flags.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0, 22: phase discipline | Done | One configuration phase; commands/results recorded |
| 5: environment configuration | Done | Local opt-in; disabled installation defaults retained |
| 26–27: evidence/research gates | Done | Unknown basis/identity/publication remain blockers |
| 28: explicit research replay | Done | Opted in; no real accounts, jobs or funding |
| 29: validation | Not in this phase | No promotion or validation claims |
| 30: operations/UI | Done | Real HTTP reads verified; no server or control mutation |
| 39: prior gap-fill boundary | Done | Explicit later opt-in; gap fill did not auto-enable |
| 40: local opt-in | Done | Effective settings, queue, HTTP and store checks passed |

## STATE SUMMARY

```text
Phase complete: local Swing research/replay opt-in.
.env: SWING_ENABLED=true; SWING_REPLAY_ENABLED=true.
docs/SPEC.md: section 40 defines explicit local opt-in.
docs/SWING_LOCAL_ENABLEMENT.md: report and verification limits.
docs/swing/SWING_LOCAL_ENABLEMENT_CHECK.json: real HTTP/hash evidence.
logs/check_swing_enablement.py: ignored read-only verification helper.
Graphify memory/reflections: navigation feedback; no rebuild.
Public API/functions added: none.
Config keys added: none; installation defaults remain false.
Mode: research; capital absent; no paper accounts or jobs.
Existing observation/history stores unchanged; other Swing stores absent.
Actual tests: 26 passed, 1 existing deprecation warning in 31.33s.
Actual HTTP: status/vintages/workspace/data-quality/accounts all 200.
Research: 0 plans; 497 failures; explicit unresolved basis/benchmark gates.
API/UI/scheduler remain stopped; start/restart API to load local flags.
Known gaps: 40 older stock histories and evidence/validation readiness.
Next phase first step: re-read SPEC and source-bundle contract, then inspect
representative original receipts before designing an evidence-aware adapter.
Stop here; await CONTINUE before the next implementation phase.
```

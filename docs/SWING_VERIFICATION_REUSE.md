# Request-scoped swing evidence verification

User-authorized P1 improvement, 22 October 2026. Master spec section 34.

## Contract and result

A full workspace request formerly verified the same vintage four times:
`view → snapshot`, `view → ideas → snapshot`,
`ideas → research.results → snapshot`, and `view → audit_vintage`.
The request now shares its successful verification through those nested calls.
It hashes all referenced evidence on first use and again before returning the
response. This retains a full closing tamper check, including same-size edits
whose modification time is restored. A size/mtime cache is deliberately avoided.

The real-archive HTTP comparison observed **four full passes reduced to two**.
The selected vintage has 19,120,769 evidence bytes per pass: bytes hashed fell
from **76,483,076 to 38,241,538**. All workspace payload fields were equal to the
uncached original composition, and the separate CSV matched the workspace export.
The subsequent request performed two new passes. These are hash-work measurements;
the single ordered timing samples include filesystem cache effects and do not
establish a general latency improvement.

## Implementation and boundaries

- `SwingRepository.verification_scope()` owns context-local receipts for a single
  synchronous workspace read. Nested scopes share the outer owner. Receipts are
  keyed by vintage, belong to one repository instance, and are reset in `finally`.
  Nothing is cached between HTTP requests. Different request threads are isolated.
- `verify(vintage)` returns the existing verification dictionary. Inside a scope,
  repeated calls reuse the successful file check while comparing current snapshot
  metadata. Outside a scope it still hashes evidence on every call.
- The closing check bypasses reuse and fully hashes the snapshot, frozen universe
  and optional reuse manifest again. A mismatch, missing artifact or metadata
  change rejects the result before the HTTP helper encodes or serves it. A failed
  body discards receipts and preserves its original exception; it returns no data.
- `universe_bytes(vintage)` returns the exact bytes whose SHA-256 was checked.
  Company names never reopen an unchecked CSV. This also prevents a temporary
  universe edit, later undone, from influencing the served names.
- Workspace `view`, `ideas` (including CSV), and `allocation` own scopes. Their
  existing research/audit calls participate without a client-provided trust flag
  or optional verification bypass. Charts and standalone research/audit operations
  retain their own checks. A standalone ideas/allocation read normally already
  had two full passes; the main measured saving is the composed workspace view.
- Visibility still uses the injected clock on every snapshot request. Synthetic
  exclusion remains in the workspace snapshot boundary. Unknown price basis,
  missing exact sessions, eligibility gates and ordinary filters remain intact.

Receipts cover the existing evidence-file verification contract; they do not add
database-to-artifact reconstruction or authenticate publisher data. SQLite remains
append-only under the existing schema/triggers. As with any finite file check, a
change after the closing check is outside that check's observation instant.

No dependency, environment setting, response field, plan/ledger schema or source
format changed. No scheduler, broker, account, import, alert or promotion action
was enabled. UI rendering was untouched in this phase.

## Files created or modified in this phase

| File | Purpose |
|---|---|
| `app/swing/repository.py` | Context-local verification receipts, final full check, frozen verified universe bytes |
| `app/swing/workspace.py` | Scope view/ideas/allocation and parse names from verified bytes |
| `tests/test_swing_verification.py` | SYNTHETIC tamper, lifecycle, concurrency, vintage and payload regression cases |
| `tests/test_swing_explanations.py` | Add a no-op scope to the existing presentation-only test stub |
| `docs/SPEC.md` | Section 34, authorized contract and boundaries |
| `README.md` | Link measured verification behavior |
| `docs/swing/VERIFICATION_REAL_CHECK.json` | Real archive HTTP comparison and protected-file hashes |
| `docs/SWING_VERIFICATION_REUSE.md` | This contract, implementation and verification report |

Prior P1 explanation/UI edits were already present and remain in the working
tree. The table lists only this phase's edits, not every uncommitted file.
Ignored local helper: `logs/verification_real_check.py`. Test logs and isolated
temporary databases stay under `logs/verification-*`.

## Commands and executed evidence

Read the complete master spec and AGENTS. Used the existing Graphify graph and
CodeGraph current source before edits. Graphify expansion from existing vocabulary:
`workspace snapshot research repository verify audit artifact reuse tamper request`.
The broad DFS output was capped; CodeGraph confirmed the exact current call paths.
No graph rebuild or extraction was performed. Graph-query/model token accounting
is unavailable; no token savings or monetary cost claim is made.

```powershell
graphify reflect --if-stale
graphify query 'workspace snapshot research repository verify audit artifact reuse tamper request' --dfs --budget 2200
.venv/Scripts/python.exe -m pytest tests/test_swing_verification.py tests/test_swing_data.py tests/test_swing_explanations.py -q -p no:cacheprovider --basetemp logs/verification-focused-approved/pytest --tb=short -o faulthandler_timeout=60
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp logs/verification-full/pytest --tb=short -o faulthandler_timeout=60
.venv/Scripts/python.exe logs/verification_real_check.py
git diff --check
```

TEMP/TMP were set to the corresponding workspace log directory. Completed test
runs used approved execution outside Windows sandbox limitations; all tests were
offline, with live tests excluded. The initial sandbox run stalled; its two
matching Python processes were stopped after the approved suite completed. That
run is not counted as a completed test result. Final `git diff --check` and report
JSON/Markdown consistency checks passed (Git emitted only CRLF conversion notices).

| Execution | Actual result |
|---|---|
| First completed focused run | `1 failed, 68 passed in 59.13s`; new vintage test incorrectly assumed clock advancement changes content identity |
| Fixture correction | Change an isolated SYNTHETIC source price row before importing the second vintage; identical imports remain idempotent |
| Corrected fixture + full offline suite | **`491 passed, 1 warning in 227.22s (0:03:47)`**, exit 0; 24 new verification cases |
| Real HTTP route probe | Exit 0; 4 → 2 full passes, next request fresh, payload/CSV equality, protected files unchanged |

New regression cases cover all three artifact types changed before/during/between
requests, same-size restored-mtime edits, deletion during a read, verified universe
byte consumption after a transient edit, nested scopes, successful/failed cleanup,
thread isolation, repository/vintage isolation, metadata corruption, missing-store
behavior, standalone verification, availability timestamps, synthetic rejection,
late tamper after research, recovery, and complete workspace payload equality.

The real probe used HTTP routes via `httpx.ASGITransport`, with an actual observed
instant frozen for deterministic comparisons. No listening server or UI was
started. It never invoked a mutation route, provider, import or worker. Existing
observations, legacy history and optional paper/operations/validation/forward
database/WAL hashes were unchanged, and absent stores stayed absent. See
`docs/swing/VERIFICATION_REAL_CHECK.json` for the recorded values.

The real universe still contains 497 members and returns zero plans with
`evaluation_unavailable`; this performance change does not repair source evidence.

## Assumptions and verification limits

Assumption: preserve full hash-based tamper detection rather than replace hashes
with filesystem timestamp trust. Two boundary checks meet that contract while
removing the repeated nested checks. Scopes are used only around synchronous
read-only workspace operations executed off the FastAPI event loop.

VERIFIED: real request hash counts and byte reduction; complete response and CSV
equality; subsequent-request fresh verification; protected real stores unchanged.
Tamper/concurrency/visibility/synthetic cases are executed offline tests only.
The full suite passed; its sole warning is the existing Starlette TestClient
use of AnyIO's deprecated `BlockingPortal` alias.
NOT VERIFIED: sustained latency/throughput, arbitrary malicious filesystem races,
publisher authenticity or profitable signals. No populated real candidate set is
available in the current archive. No deployment or new browser QA was performed.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0, 22 — phase discipline | Done | One authorized improvement; evidence/report and handoff; stop afterward |
| 2, 3, 5 — integrity/stack/config | Done | Synthetic tests isolated; no fabricated live data, dependencies or settings |
| 17, 18 — HTTP/UI | Done | Payload/CSV preserved; existing thread offload retained; UI unchanged |
| 20 — tests | Done | 491 offline tests passed, including 24 new verification cases |
| 26 — evidence provenance | Done | Fresh boundary hashes, vintage isolation and original unknowns preserved |
| 27–31, 33 — swing behavior | Done | Rules, risk, ledgers, source gates and P1 explanations preserved |
| 32 — source repair | Not in this phase | Existing data gaps remain unresolved |
| 34 — request reuse | Done | Scoped receipts plus final full verification and verified CSV bytes |

## STATE SUMMARY

```text
Phase: P1 request-scoped verification reuse.
app/swing/repository.py: verification receipts and full closing check.
app/swing/workspace.py: request scopes and verified universe parsing.
tests/test_swing_verification.py: isolated verification regression cases.
tests/test_swing_explanations.py: scope support in an existing test stub.
docs/SPEC.md: section 34; README.md: feature reference.
docs/SWING_VERIFICATION_REUSE.md: contract, evidence and handoff.
docs/swing/VERIFICATION_REAL_CHECK.json: real comparison/protected hashes.
Public: SwingRepository.verification_scope() -> context manager.
Public: verify(vintage: str) -> dict (same response).
Public: universe_bytes(vintage: str) -> bytes (checked frozen content).
Config/dependencies/schema added: none.
Decision: two full boundary passes; nested calls reuse within one request.
Real result: 4 -> 2 passes, equal payload/CSV, protected stores unchanged.
Tests: 491 passed, 1 dependency warning in 227.22s; real probe exit 0.
Known issue: current source data gaps still yield zero real plans.
Prior P1 changes preserved; no commit, deployment or worker enabling.
Next: await explicit CONTINUE; reread docs/SPEC.md before another phase.
```

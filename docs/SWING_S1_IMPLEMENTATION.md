# Swing S1 — data and provenance implementation

Date: 16 October 2026. Scope: S1 only of [the swing plan](SWING_TRADING_PLAN.md). S2–S6 have not started.

## Delivered

- Separate swing observations, reports and evidence in `data/swing.db` / `data/swing/`; no source-project runtime imports or schema sharing.
- Typed daily-history and context protocols with explicit vintage, as-of time, adjustment basis and unavailable/conflicting states. Existing provider contracts remain compatible.
- Source cache reader using SQLite `mode=ro`, query-only protection and a consistent transaction. Export allowlisted price/delivery/benchmark fields for the supplied universe only; source credentials/account tables are excluded.
- Frozen content-addressed JSONL, universe bytes, selected schema/calendar policy, code fingerprints and a pinned 14-file cross-project reuse manifest. No source Git revision was available for the inspected sibling copies; per-file SHA-256 values are the recorded references.
- Bounded import windows: 400 expected completed price sessions and 25 delivery sessions by default. No inferred prices/volumes, benchmark substitution, historical identities or publication timestamps.
- Canonical observations and rejects commit atomically with the snapshot/report. Interrupted imports roll back. Duplicate symbol-session identities fail rather than selecting an arbitrary record. Committed observations cannot be updated, deleted or extended.
- Identical frozen inputs/policy reuse the existing snapshot; changes create a separate vintage. Import completion is the visibility gate, so historical as-of requests cannot backdate local availability.
- Per-symbol exact-session gaps, current-series expectations, price-basis uncertainty, delivery freshness and benchmark close/open coverage. Known-basis readers decline unknown or mixed bases and missing required sessions.
- CLI `import`, `report` and `verify`; source files and destination/evidence cannot be overwritten by report output. No network, broker orders, UI integration or scheduling was added.

## Actual real-data verification

The final real import's snapshot ID is:

```text
54e87b147932a02732f6931adf6fb8067c9a4f2d2084126e816f1d94a97d10aa
```

The source was the sibling multibagger project's public `lite.db` cache. The current supplied NSE CSV yielded 497 eligible symbols; DUMMY/RR exclusions and BE retention use the existing universe rules.

| Check | Actual result |
|---|---|
| Selected source observations | 105,744 |
| Canonical price observations | 94,150 |
| Canonical delivery observations | 10,862 |
| Symbols with price observations | 241 of 497 |
| Symbols with delivery observations | 494 of 497 |
| Contradictory delivery fields | 493 rows on 2026-09-11 quarantined |
| Unsupported calendar session | 239 price rows on 2025-10-21 quarantined |
| Configured Nifty 500 (`^CRSLDX`) | Absent from the source's benchmark table |
| Source benchmark inventory | Nifty 50, Midcap 150, Smallcap 250; none substituted |
| Import cutoff | Last completed session 2026-10-16 |
| Latest source price observations | Latest date 2026-10-15; several symbols older |
| Latest delivery observations | 2026-10-14 |
| Complete required trailing windows | Zero at this cutoff |
| Eligible swing histories | Zero; adjustment basis and historical identity unverified |
| Repeat import | Same snapshot ID, `reused=true`; no appended observations |
| Evidence verification | Snapshot, universe and frozen reuse-manifest hashes verified |
| SQLite checks | `integrity_check=ok`; no foreign-key violations |

The delivery percentage/quantity/volume fields disagree beyond their declared two-decimal rounding allowance. The importer does not guess which field is wrong or silently recalculate the percentage. The 2025-10-21 date is unsupported by the configured proxy calendar; that is **not** proof that the source price rows are invalid exchange observations. Special-session evidence/hours need separate review before using those rows.

Historical series/ISIN cannot be inferred from today's constituent CSV. Original cache rows lack row-linked adjustment evidence and verified historical publication, so price basis remains unknown. These observations are useful for inspection and coverage discovery, not validated historical fills or trading signals. More source records than the selected windows exist, but their completeness/availability is not implied by this import.

The [compact evidence summary](swing/S1_COVERAGE_SUMMARY.json) is retained in the repository. Full per-symbol coverage and raw frozen evidence are local ignored artifacts under `logs/swing-s1/` and `data/swing/`. Developmental policy vintages are retained; none was overwritten when validation/selection-policy fingerprints changed.

## Files created / modified

| File | Purpose |
|---|---|
| `app/swing/__init__.py` | Isolated S1 package |
| `app/swing/models.py` | Daily/context observations and supplemental request/provider contracts |
| `app/swing/repository.py` | Transactional schema, immutable store, as-of readers and evidence verification |
| `app/swing/data.py` | Canonical validation, rejects, bounded atomic import and coverage |
| `app/providers/swing_archive.py` | Read-only consistent export, frozen current-universe selection and evidence hashes |
| `scripts/swing_data.py` | Explicit CLI import/report/verify with injected configuration/time |
| `app/core/config.py`, `.env.example` | Validated S1 paths, windows, benchmark and import bounds |
| `.gitignore` | Ignore local swing evidence; existing `data/*.db*` exclusions cover the store |
| `tests/fixtures/swing_source.py` | Labelled fictitious TEST-only source cache/universe builder |
| `tests/test_swing_data.py` | Source immutability, isolation, availability, quality, rollback and CLI tests |
| `docs/swing/SOURCE_MANIFEST.json` | Inspected sibling source hashes/revision availability |
| `docs/swing/S1_COVERAGE_SUMMARY.json` | Actual final aggregate verification evidence |
| `docs/SPEC.md` §26 | Frozen S1 contract and defaults |
| `README.md`, this report | Usage, verification limits and handoff |

The existing uncommitted root redirect in `app/main.py` and its test in `tests/test_api.py` were preserved. The previously created `docs/SWING_TRADING_PLAN.md` remains a separate planning artifact. No commit/push, production scan or preview restart was performed in S1.

## Commands and test evidence

Read AGENTS/SPEC/plan; inspected the pre-existing dirty diff; used CodeGraph to inspect relevant current/source contracts; read only the sibling public schema/provenance aggregates; generated/checked source hashes; executed:

```powershell
$taskTmp=Join-Path (Get-Location).Path ('logs/swing-s1-baseline-'+[guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe -u -m pytest -q -p no:cacheprovider --basetemp $taskTmp
# During development: isolated tests/test_swing_data.py, then affected universe/config tests.
.venv/Scripts/python.exe -m compileall -q app/swing app/providers/swing_archive.py scripts/swing_data.py
.venv/Scripts/python.exe -m scripts.swing_data import --source "D:/Codex/Finding Multibagger NSE/multibagger634-main/lite.db" --report "logs/swing-s1/final-coverage.json"
.venv/Scripts/python.exe -m scripts.swing_data import --source "D:/Codex/Finding Multibagger NSE/multibagger634-main/lite.db" --report "logs/swing-s1/final-repeat-coverage.json"
.venv/Scripts/python.exe -m scripts.swing_data verify --vintage 54e87b147932a02732f6931adf6fb8067c9a4f2d2084126e816f1d94a97d10aa
git diff --check
git status --short
```

Actual baseline: `231 passed, 1 warning in 75.05s`. Intermediate full suite: `260 passed, 1 warning in 97.87s`. After frozen-universe race/comment handling: affected S1/universe/config tests `44 passed in 26.86s`. A later release run exposed a WAL-sensitive source-file hash assertion: SQLite commit context managers leave connections open, so fixture writer cleanup could checkpoint later. Explicit fixture-writer and archive-reader closure corrected the issue. The final full suite below includes that fix. The warning is the existing Starlette/AnyIO BlockingPortal deprecation.

The first restricted Windows test launch stalled without output and was interrupted; the successful offline runs used the approved launcher outside that restriction, with fresh workspace-local test directories. No automatic approval rejection occurred. No test data was served by the production API/UI.

Final full-suite output:

```text
261 passed, 1 warning in 97.89s (0:01:37)
```

Compilation, whitespace/document reference checks and the final evidence verification also passed.

## Assumptions, VERIFIED and NOT VERIFIED

**Assumptions:** import only public caches from the explicitly supplied source; current CSV defines the selected inspection universe, not a historical constituent list; default windows are coverage targets, not claims of adequate strategy evidence; source cache prices have unknown adjustment basis until independently reconciled; local import time controls availability; source projects and existing scanner contracts remain independent.

**VERIFIED:** supported source schema; exact selection and DUMMY/RR/BE behavior; read-only consistent source snapshots during concurrent writes; immutable versioning and real-repeat idempotency; tamper checks; current real coverage/rejection totals; source file hashes unchanged since inspection; transactional rollback; naive/future timestamp rejection; no invented index opens/volumes; known-basis and exact-session query gates; missing context states; SQLite integrity/FKs; CLI/report output protection; legacy regression baseline and recorded tests.

**NOT VERIFIED:** correctness/completeness of source historical data; genuine special-session hours; verified historical adjustment/identity/publication; current Nifty 500 benchmark history; live NSE/FYERS access or credentials; historical constituent coverage; broker execution, signal profitability or institution-ready status. S1 does not implement setup generation, risk allocation, paper fills or UI changes.

## SPEC compliance

| Section | Status | Evidence / remaining limit |
|---|---|---|
| 0/22 — phase discipline/report | Done | S1 only; actual commands/results, limits and state included |
| 1/6/10/12 — universe/legacy strategy | Done | Existing eligible-universe rules reused; legacy filters/scores unchanged |
| 2/3 — integrity/stack | Done | Real bounded archive import; synthetic fixtures isolated; no dependencies added |
| 5 — configuration | Done | Eight S1 settings documented/validated; no account capital invented |
| 7/8/9 — providers/hygiene/time | Done | Supplemental protocols, explicit vintages, unknown basis and calendar gates |
| 14 — persistence | Done | Separate transactional immutable store/evidence; no legacy history rewrite |
| 15/17/18 — scheduler/API/UI | Not in this phase | CLI only; no scheduler hooks or UI/internal-import changes |
| 20 — tests | Done | Baseline, affected tests and final-suite evidence recorded |
| 23–25 — prior extensions | Done | Existing context/alerts/score behavior preserved |
| 26 — S1 | Done | Foundation/import gates met; quantified source limitations prevent trade eligibility |

## STATE SUMMARY

```text
Completed: swing S1 data/provenance foundation, 16 Oct 2026; S2 not started.
app/swing/__init__.py: isolated research package.
app/swing/models.py: DailyPrice, DatedContext, HistoryRequest/Result, ContextRequest;
  DailyHistoryProvider.history(request), ContextProvider.context(request), utc(value).
app/providers/swing_archive.py: load_selection(path), export_snapshot(...), file_hash(path).
app/swing/repository.py: SwingRepository(path,timeout,calendar=None); report/verify/history/context.
app/swing/data.py: import_snapshot(snapshot,repository,calendar,config,clock); coverage(...).
scripts/swing_data.py: run_import(config,clock,source); CLI import/report/verify.
app/core/config.py + .env.example: eight validated S1 data settings.
.gitignore: excludes local data/swing evidence.
tests/fixtures/swing_source.py: SYNTHETIC TEST-only source builder.
tests/test_swing_data.py: read-only, consistency, atomicity, timing, gates and CLI tests.
docs/swing/SOURCE_MANIFEST.json: hashes of 14 inspected sibling files; no Git revisions available.
docs/swing/S1_COVERAGE_SUMMARY.json: actual final aggregate verification.
docs/SPEC.md §26: frozen S1 contracts/defaults.
README.md + docs/SWING_S1_IMPLEMENTATION.md: usage, evidence and limits.
Preserved prior docs/SWING_TRADING_PLAN.md and existing main.py/test_api.py dirty changes.
New keys: SWING_DB_PATH, SWING_ARTIFACT_DIR, SWING_SOURCE_DB_PATH,
  SWING_MIN_HISTORY_SESSIONS, SWING_DELIVERY_SESSIONS, SWING_IMPORT_BATCH_SIZE,
  SWING_SOURCE_TIMEOUT_SEC, SWING_BENCHMARK. Existing SQLITE timeout reused.
No trading, capital or runtime scan flags enabled.
Store: data/swing.db; artifacts data/swing; full reports logs/swing-s1 (ignored).
Final vintage: 54e87b147932a02732f6931adf6fb8067c9a4f2d2084126e816f1d94a97d10aa.
Real data: 94,150 prices + 10,862 delivery observations; 732 rejected rows retained.
Coverage: 241/497 price symbols; 494/497 delivery symbols; source benchmark absent.
Unknown basis/identity/publication and stale/gapped coverage keep eligibility at zero.
Final import repeated with same ID/reused=true; evidence and SQLite/FKs verified.
Tests: 261 passed, one existing dependency warning; compilation/whitespace checks passed.
No network or broker requests in this phase.
Known limits: 493 contradictory delivery rows, 239 unsupported-calendar price rows;
  no claim that an unsupported calendar date proves an invalid source observation.
Next after CONTINUE: reread SPEC/plan and S1 coverage; freeze S2 setup/risk contracts,
  keeping unavailable-basis/benchmark gates closed until supporting evidence exists.
Stopped after S1; no S2 implementation, commit/push or production scanner changes.
```

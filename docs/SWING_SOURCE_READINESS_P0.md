# P0 — swing source-readiness audit and repair scope

Date: 22 October 2026. Phase: the P0 priority selected after the graph review;
this is not a rerun of the original project foundation Phase 0.
The authorized deliverable is a fresh audit and a concrete source-repair contract.
Audit complete; real source repairs and a replacement importer are not implemented.

## Result

The imported archive is insufficient for the frozen daily swing requirements.
More price rows alone will not make it eligible: the current public-cache
adapter deliberately cannot attest raw trading basis, historical identity or
publication evidence. Its v1 validation audit explicitly retains those blockers.

The fresh machine-readable evidence is
[P0_READINESS_SUMMARY.json](swing/P0_READINESS_SUMMARY.json). All counts below
refer to that observed archive, with the configured calendar and decision cutoff.
The upstream cache's current row contents could not be queried during this audit.

| Check | Fresh result |
|---|---|
| Audit instant | 22 October 2026, 13:47:25 IST |
| Latest completed configured session | 21 October 2026; today's active bar excluded |
| Latest imported vintage | `54e87b147932a02732f6931adf6fb8067c9a4f2d2084126e816f1d94a97d10aa` |
| Latest import | 16 October 2026 |
| Real stored vintages verified | 3 |
| Frozen universe matches current configured file | Yes; 497 eligible symbols |
| Symbols with any imported prices | 241; 256 have no prices |
| Complete 400-session histories | 0 |
| Symbols with the cutoff-session price | 0 |
| Price requirement window | 5 March 2025–21 October 2026, 400 configured sessions |
| Required price symbol/session pairs | 198,800 |
| Observed price pairs in that window | 93,692 |
| Missing price pairs | 105,108 |
| Delivery requirement window | 15 September–21 October 2026, 25 configured sessions |
| Observed / missing delivery pairs | 10,369 / 2,056; zero complete delivery histories |
| All imported price rows / basis | 94,150 / `unknown` |
| Nifty 500 benchmark rows | 0; all 400 required dates missing |
| Imported context kinds | Delivery only; 10,862 observations in the frozen import |
| `SWING_SOURCE_DB_PATH` | Empty; the audit used the source URI recorded in the snapshot |
| Source/cache and protected store hashes | Unchanged during audit |
| Swing-store quick check / foreign-key violations | `ok` / 0 |

Observed pair counts exclude stored observations outside today's requirement
window; they are not the same as the total imported row counts. Missing pairs
are coverage diagnostics, not instructions to fabricate 105,108 observations.
New listings and genuinely unavailable sessions remain insufficient history.

## Findings and concrete repairs

### R1 — Supply the benchmark and enough real stock history

Obtain actual Nifty 500 **price-index** history for the configured benchmark,
including observed opens for execution/baseline work and closes for indicators.
Retain missing index volume as null. No Nifty 50, ETF, total-return index or
proxy substitution. Obtain full valid OHLCV for each selected security with
enough real sessions to pass `required_sessions(config)`; it is 400 today.
Do not shorten warm-up or invent pre-listing observations to make all 497 pass.

### R2 — Resolve price basis and date-valid security identity

A cache's positive numeric prices do not establish that its OHLC is raw.
The new import path needs row-linked source evidence, symbol/series/ISIN valid
on the observation date, and supported corporate-action/identity transitions.
Retain original observations and action evidence separately. Do not overwrite
older snapshots or combine raw and adjusted bars silently.

### R3 — Represent availability and context explicitly

The archive's import time proves local availability only. Retrieving a historical
file today does not establish when it was available to a decision last year.
Freeze observed/ingested timestamps and retain publisher timestamps only when
supported by evidence. Missing historical timing stays unverified.

Import date-valid sector, earnings/event and surveillance context for the
existing gates, with explicit unknown/conflicting states. Delivery is context
under the present v1 setup rules, not an additional mandatory setup condition;
do not add a delivery filter while repairing data.

### R4 — Correct the special-session calendar policy in a separate implementation

The frozen import rejected 239 rows on 21 October 2025 as `NON_SESSION`.
NSE's capital-market circular confirms a special 13:45–14:45 IST normal-market
session on that date: [NSE/CMTR/70319, 22 September 2025](https://nsearchives.nseindia.com/content/circulars/CMTR70319.pdf).
The configured proxy has no session for it. This is a confirmed calendar-policy
gap, not proof that those source prices are malformed. Accepting them would
still require normal row validity and provenance checks.

Implement versioned NSE special-session bounds, preserve the global XBOM cache,
and create a new import vintage under the changed policy. Recompute exact-session
requirements and explicitly audit schedule effects. Existing resolved cohorts
and frozen evidence remain immutable. A one-hour session cannot form the user's
complete four-hour candle. No calendar behavior was changed in this phase.

### R5 — Reconcile delivery contradictions from the original source

The frozen import rejected 493 delivery observations on 11 September 2026 because
reported quantity, volume and percentage disagree beyond the declared rounding.
Reacquire the source records and retain both old and revised evidence. Do not
guess which field is wrong, recalculate the source percentage as a silent fix,
or assume BE delivery equals 100%. That rejection date is outside today's
25-session delivery window; it does not explain all 2,056 current gaps.

### R6 — Add a genuine evidence-backed adapter path

The existing adapter writes `price_basis=unknown` and false publication/identity
flags (`app/providers/swing_archive.py`, `app/swing/data.py`).
`SwingResearchService.results` does not attest trading basis or identity from
this adapter, and `audit_vintage` unconditionally records the v1 historical
limitations. Fetching better files through the unchanged cache importer cannot
resolve those structural limits.

Implement a separately versioned bundle verifier/provider path with explicit
capabilities, preserving v1's inspection behavior. The proposed contract is in
[SWING_SOURCE_BUNDLE_CONTRACT.md](SWING_SOURCE_BUNDLE_CONTRACT.md). The real-file
schema mapping must be frozen only after representative source files are read.
No boolean flag or manually edited header is sufficient evidence.

## Source route researched in this phase

Recommended first route: explicitly supplied exchange files, imported locally
through the new contract. This avoids committing the project to a subscription
or an undocumented endpoint before coverage is demonstrated.

- [NSE All Reports](https://www.nseindia.com/all-reports) lists final cash-market
  UDiFF bhavcopy, delivery-position and security reports. These are candidate
  source products; complete target-window retrieval and field mappings have not
  been verified by this audit.
- [NSE Indices historical data](https://www.niftyindices.com/reports/historical-data)
  exposes historical index OHLC reports. A successfully retrieved Nifty 500
  dataset for this project is still required.
- [NSE Indices data subscription](https://www.niftyindices.com/offerings/data-subscription)
  describes historical/ongoing index and constituent data. Availability of
  complete point-in-time Nifty 500 membership for this use must be confirmed.
- [NSE EOD/historical products](https://www.nse.in/static/market-data/eod-historical-data-subscription)
  are an alternative if supplied public files cannot establish required coverage.
  No signup, purchase or provider message was made.

The pages establish candidate product categories, not dataset completeness,
historical publication verification or access to every required context field.
Current Yahoo prices alone do not address the missing evidence contract.

## Read-only audit limits

The recorded upstream source exists at its saved local file URI, but its first
public-table query returned `sqlite3.OperationalError: unable to open database
file` in this read-only environment. The underlying cause was not established.
No `immutable`/`nolock` bypass or whole-database copy was used; bypassing WAL state
could hide committed data, and copying unrelated source tables is out of scope.
Current upstream row counts remain NOT VERIFIED. The imported store and evidence
were readable, and all three real vintages passed their existing hash checks.

All reported gaps use the existing calendar policy. Fixing the confirmed special
session will change the exact 400-session date set; the pair total must be
recomputed for a new vintage rather than rewritten into this evidence record.

## Implementation handoff

The next phase should implement only the local bundle validation/import seam
and the approved calendar policy, with synthetic fixtures and no network feed.
Its first step is to inspect representative real equity, index, identity/action
and dated-context files and freeze their mappings. If those inputs are absent,
report the missing source evidence rather than pretending to complete a real
repair. All fields and gate behavior must be documented before code changes.

Acceptance before claiming repaired readiness:

1. Prior v1 snapshots remain loadable and unverified where they were unverified.
2. New source evidence produces a new immutable vintage; source files stay intact.
3. Exact-date prices/benchmark and all existing required evidence pass independently.
4. Rejections distinguish coverage, invalid records and unresolved source evidence.
5. Special sessions have sourced bounds and an explicit versioned policy.
6. Changed source revisions do not overwrite existing history, fills or outcomes.
7. Any sample with insufficient listing history remains unavailable.
8. Historical validation and prospective operational readiness remain distinct;
   neither improved coverage nor 30 prospective sessions proves an edge.

## Files, commands and verification

Created: this report; `docs/SWING_SOURCE_BUNDLE_CONTRACT.md`;
`docs/swing/P0_READINESS_SUMMARY.json`. Modified: README links and the master
spec's audit-only scope note. Local ignored helpers:
`logs/p0_source_audit.py`, `logs/p0_verify_audit.py`; the per-symbol exact-date
repair list is `logs/p0-source-repair-details.json`.

Commands executed from the project root:

```powershell
graphify reflect --if-stale
graphify query "coverage" --budget 1000
.venv/Scripts/python.exe logs/p0_source_audit.py
.venv/Scripts/python.exe logs/p0_verify_audit.py
# TEMP/TMP and basetemp were unique workspace-local logs directories.
.venv/Scripts/python.exe -m pytest tests/test_swing_data.py tests/test_swing_setups.py -q -p no:cacheprovider --basetemp <unique-workspace-temp>/pytest --tb=short
```

Also read AGENTS/SPEC/contracts, inspected selected Settings values only, queried
CodeGraph source/flows, checked Git status, and opened the official source pages.
The first audit attempt exited 1 at the upstream source query. The final audit
records that unavailable state explicitly and exits 0; it did not repair the
upstream source or infer its contents.

Actual test output: **`56 passed in 32.12s`**, exit 0. Independent read-only SQL
reconciled the price/delivery pair totals, returned `quick_check=ok` and zero
foreign-key violations. Before/after hashes of the source, universe and protected
existing stores matched; missing stores were not created. Hashes describe the
files checked, not an authentication guarantee for the data publisher.

Audit-helper syntax, JSON evidence and Markdown fence/whitespace checks passed.
`git diff --check -- README.md docs/SPEC.md` exited 0. The unscoped Git whitespace
check identified a pre-existing blank line at `ui/styles.css:373`; that unrelated
file was not changed in P0. Git's overall diff sizes include earlier user work,
not just the small README/spec additions made here.

Graphify reused the existing graph; no new extraction calls were made.
Model/query token accounting is unavailable; no monetary cost or token savings
is claimed.

VERIFIED: observed imported coverage, exact-date gaps under current policy,
three vintage evidence checks, current-universe hash match, special-session
circular, targeted tests, independent count reconciliation and file hashes.

NOT VERIFIED: current upstream cache contents, full replacement-source coverage,
historical publication/membership evidence, replacement adapter behavior, API/UI,
full regression suite, live downloads, prospective sessions or profitability.

## SPEC COMPLIANCE

| Sections | Status | Note |
|---|---|---|
| 0: phase discipline/reporting | Done | P0 audit only; implementation waits for CONTINUE |
| 2, 6: integrity/universe | Done | Real supplied universe used; no fabricated repair rows |
| 7–8, 26: provider/hygiene/provenance | Done | Fresh audit and proposed contract; existing checks preserved |
| 20: tests | Done | Relevant existing tests and independent audit checks executed |
| 27–31: swing/paper/validation | Not in this phase | No strategy/ledger/promotion behavior changed |
| 32: source-readiness audit | Done | Evidence and handoff delivered; real repairs remain pending |

```text
STATE SUMMARY
Completed: P0 source-readiness audit and repair contract, not data repair.
Created: docs/SWING_SOURCE_READINESS_P0.md — current findings and repair order.
Created: docs/SWING_SOURCE_BUNDLE_CONTRACT.md — proposed evidence-backed inputs.
Created: docs/swing/P0_READINESS_SUMMARY.json — fresh aggregate evidence.
Modified: README.md — audit/contract links; docs/SPEC.md — P0 scope note.
Local helpers: logs/p0_source_audit.py, logs/p0_verify_audit.py.
Local detail: logs/p0-source-repair-details.json — per-symbol missing sessions.
Public classes/functions/config keys added: none.
Tests: 56 passed in 32.12s; independent SQL/count/hash checks passed.
Decision: retain old adapter unknowns; require evidenced new input path.
Known issues: incomplete/stale imported data, no benchmark, source query unavailable.
Confirmed calendar repair: 2025-10-21 Muhurat session, NSE/CMTR/70319.
Assumptions: current supplied universe; existing calendar retained during audit.
Next: after CONTINUE reread SPEC and inspect representative real source files.
Stop: no source import, calendar implementation or later phase started.
```

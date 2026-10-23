# Local market-data reuse — 23 October 2026

The user identified `D:/Codex/Finding Multibagger NSE`. Its data is now reused in
two separate inspection vintages in this screener. The broader one contains
observed stock history for all 497 supplied-universe members and 398 accepted
Nifty 500 OHLC rows. The previous imported snapshot had prices for 241 members
and no benchmark observations. Data availability in a sibling project and data
already imported into this app were different states.

## Results at a common completed-session cutoff

The comparison uses the same 497-member supplied universe, existing calendar,
400 required sessions from 6 March 2025 through 22 October 2026, and 198,800
required stock/session pairs. It describes coverage keys, not interchangeable
price bases or revisions of the same dataset.

| Check | Original frozen import | Refreshed provider cache | Separate NSE UDiFF collection |
|---|---:|---:|---:|
| Symbols with prices in the required window | 241 | 241 | 497 |
| Observed stock/session pairs | 93,463 | 94,687 | 191,770 |
| Missing stock/session pairs | 105,337 | 104,113 | 7,030 |
| Complete current 400-session histories | 0 | 2 | 0 |
| Latest stock price | 15 October or earlier | Up to 22 October | 21 October for all 497 |
| Accepted Nifty 500 OHLC/open rows | 0 | 398 | 398 |

The raw collection adds 98,849 stock/session coverage keys absent from the
original import. It also lacks 542 keys present in that different provider-cache
dataset; those are not silently copied into raw history. Exactly 457 symbols
are missing only the cutoff session, 22 October. The other 40 have older gaps
as well; their exact dates are retained in the delta report. No price is filled
before listing or on a missing/non-traded date.

Benchmark receipts contain 399 Nifty 500 rows from 6 March 2025 through
19 October 2026. Existing S1/calendar validation accepts 398; the current
400-session requirement still lacks **21 and 22 October 2026**. No Nifty 50,
Midcap, Smallcap, ETF or TRI substitutes were used. Canonical benchmark basis
remains unknown under the existing S1 adapter, even though original index report
bytes and exact mapped OHLC are now retained.

The final raw collection selected 192,246 equity rows, 399 index rows and
11,854 delivery rows. S1 accepted 192,168 price rows (191,770 equity plus 398
index) and all 11,854 contexts; it rejected 477 rows as NON_SESSION. The identity
`192168 + 11854 + 477 = 204499` reconciles every staged row. Raw parsing recorded
zero per-row rejects. Existing calendar policy, including its already documented
special-session limitation, is unchanged. Calendar fixes still require a new
policy/vintage rather than editing these observations.

## Source and selected products

Read-only source:
`D:/Codex/Finding Multibagger NSE/multibagger634-main/lite.db` (11,705,696,256 bytes
at inspection). Initial public-cache inventory contained 616,613 prices across
635 symbols through 22 October and 2,460,232 delivery rows across 3,348 symbols.
Those counts describe the source scope, not this screener's supplied universe.

Its `benchmarks` table contained Nifty 50/Midcap 150/Smallcap 250 only. The exact
Nifty 500 series was found in original CSV blobs referenced by
`nse-index-close-observed-v1`, using `Index Name`, `Index Date`, and the observed
open/high/low/closing-value columns. It had not been exposed by that project's
three-index cache. Source URLs, document IDs, SHA-256, ingestion/publication
metadata and original CSV bytes accompany the collected observations.

The source ID `nse-bhavcopy-observed-v1` contains two products for the same dates.
Raw extraction explicitly selects final cash-market UDiFF files named
`BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`. It retains 400 original equity
documents and lists 400 security/delivery product documents as excluded.
Competing revisions of the chosen product still fail for review. Current
provider cache prices are kept in their own vintage and never fill raw gaps.
Only observed EQ/BE rows matching supplied symbols are selected. Series/ISIN
remain in original receipts; S1 does not infer historical identity from today's
universe file.

The source was opened with ordinary SQLite `mode=ro`, `query_only=ON` and a
consistent transaction. No immutable/nolock option, source runtime imports,
whole-database copy, credentials or account-table export were used. Database and
nonempty WAL hashes bookend each successful collection. All bookends matched.
An empty SQLite WAL has no committed frames and is normalized to absent; transient
SHM metadata is not treated as market data. Any nonempty WAL is hashed normally.

## Stored artifacts and access

New provider-cache vintage:
`e33f2e7f95d69b2702d727661552606287c28e6c16ffb73dcedf4387b3a72283`.
Broader raw-source vintage:
`4d8e48f8d01212ae49f07eb7a5940b39f6d8bdc5aadbcd9d0505fc805065b6e4`.

Ignored runtime artifacts under `data/swing/local_sources/20261023/` and
`data/swing/local_sources/20261023-raw-final/` contain public.db, manifest.json
and content-addressed source receipts. Existing S1 import produced new hashed
JSONL snapshots under `data/swing/snapshots/` and appended canonical observations
to `data/swing.db`. Three original vintages and the first new cache vintage were
preserved when adding the raw vintage. Snapshot/report/price/context/reject logical
row hashes matched before/after for every pre-existing vintage. All five vintages'
artifact/universe evidence checks passed. Repeat imports reused their snapshot IDs.

Machine-readable evidence:

- `docs/swing/LOCAL_SOURCE_REUSE_REAL_CHECK.json`: first cache import, protection checks.
- `docs/swing/LOCAL_SOURCE_RAW_REAL_CHECK.json`: raw import, independent counts and HTTP checks.
- `docs/swing/LOCAL_SOURCE_COVERAGE_DELTA.json`: same-cutoff comparison and per-symbol gaps.

Read-only HTTP calls through ASGITransport returned 200 for vintage discovery and
workspace inspection, with summary values matching the new stored report.
Actual output included `plans=0`, `view_state=disabled`, `scheduler_running=false`.
Persisted SWING_ENABLED and SWING_REPLAY_ENABLED remain false. No process was
started to serve a browser, and no opt-in flag was changed. The data is discoverable
in Trade Ideas source inspection when the existing app is run.

## Commands, tests and independent checks

Reread SPEC and the source/bundle contracts. Graphify reused the 1,799-node,
5,876-edge map with expanded terms `archive cache coverage snapshot source
benchmark prices import`, DFS and a 1,000-token budget. The traversal was
truncated and used for navigation only; no extraction/model-token savings claim.
CodeGraph returned current importer/provider/validation/workspace source and
source-project price/archive paths. Source inventory SQL read only public
market-data/document tables. Refined filesystem discovery excluded test/virtualenv trees;
synthetic sibling databases were not used for real imports.

Executed synthetic pytest selections used the project venv, isolated TEMP/TMP,
`-q -p no:cacheprovider --tb=short` and unique workspace basetemps:

```text
tests/test_swing_local_source.py tests/test_swing_data.py
tests/test_swing_setups.py tests/test_swing_verification.py
```

Actual run history:

- Initial: 1 failed, 90 passed in 65.78s. A read-only WAL-mode fixture created a
  zero-byte WAL. A separate synthetic reproduction confirmed unchanged main DB
  bytes and a zero-byte sidecar; normalization preserves nonempty WAL detection.
- Corrected cache/extraction run: 93 passed in 66.71s.
- Expanded raw-source/import/evidence selection: 100 passed in 68.31s.
- Final schema-guard collector selection: 21 passed in 7.74s.
- Final explicit-product collector selection: 21 passed in 7.29s.

The 21 new collector cases cover exact index identity/date, duplicate rows,
invalid/nonfinite OHLC, missing index volume, hash mismatch, naive/future
ingestion, wrong source URI, source-change detection, conflicting index revisions,
read-only selected export, private-table exclusion, idempotent S1 import, retained
unknown basis, malformed/ambiguous equity rows, zip/legacy mappings, unsupported
columns, explicit product exclusions and no adjusted/raw stitching. The existing
import/setup/tamper tests also passed in the affected selection. The full suite
was not rerun for this CLI/data-only phase; the previous full run was 581 passes.

Real commands (all use `.venv/Scripts/python.exe`):

```text
python -u logs/reuse_local_real.py
python -u logs/reuse_local_real.py --raw
```

The first raw collection stopped before manifest/import because two different
source products shared the source ID. Public metadata established that they were
UDiFF versus security/delivery products, not interchangeable revisions. After
freezing the explicit UDiFF mapping and testing it, a fresh directory was collected
and imported successfully. The unpublished diagnostic directory is ignored and
was not served/imported. Successful run logs are `logs/local-source-real-run.txt`
and `logs/local-source-raw-final-run.txt`; failures/tests are retained separately.

The helper called the new collector and existing `scripts.swing_data.run_import`,
then verified idempotence, old-vintage logical hashes, new snapshot evidence,
independent per-symbol session sets/counts, `PRAGMA quick_check=ok`, protected
file hashes and read-only HTTP reads. Protected files included legacy history,
paper/operations/validation/forward stores and WALs, `.env`, universe and settings.
Their hashes/absence remained unchanged. Source bookend hashes are in the frozen
collection manifests. Selected staging databases also retained their hashes.
Additional independent SQL generated the current-cutoff delta without changing
the source or previously imported records. `git diff --check` passed with only
line-ending conversion notices. No dependency or configuration keys were added.

## Assumptions and VERIFIED / NOT VERIFIED

Assumption: use the user-supplied current universe as current selection only,
retain existing calendar/400-session requirements, and reuse local observations
without downloads. Different source products/bases stay separate. Source URLs and
hashes establish recorded origin/stability, not independent publisher authenticity
or historic availability. The collection manifest is an audit sidecar; existing
S1 snapshot verification does not grant its metadata new provider capabilities.

VERIFIED: both imports, 497-symbol raw coverage, 398 accepted benchmark opens/OHLC,
exact gaps, counts/reject reconciliation, source hashes, preserved old vintages,
unchanged protected stores, repeated-import IDs, SQLite quick check, HTTP source
inspection and the executed tests above.

NOT VERIFIED/remaining: complete current 400-session coverage; 22 October stock
bhavcopy and 21/22 October index inputs; older gaps for 40 symbols; a versioned
evidence-backed adapter for raw basis, dated identity/actions and required context;
historical publication/membership, corrected special-session policy, current signal
eligibility, validation/promotion, live scan/UI/browser operation or profitability.
All canonical S1 prices still have unknown basis. No gate was weakened, calendar
changed, worker started, account funded, alert sent, source downloaded, commit or
deployment performed. Four-hour integration remains pending.

## Files created or modified

Created: `scripts/swing_reuse_local.py` (explicit local collector),
`tests/fixtures/swing_local_source.py` (labelled synthetic source files),
`tests/test_swing_local_source.py` (21 cases), `docs/SWING_LOCAL_SOURCE_CONTRACT.md`,
this report, and the three JSON evidence/delta files listed above.
Modified: `docs/SPEC.md` section 38 and README source-reuse status.
Runtime data: two new immutable S1 vintages in data/swing.db and ignored evidence/
collection files under data/swing. Ignored helper `logs/reuse_local_real.py` and
logs/basetemps support reproducibility. All earlier uncommitted code retained.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0, 22 — phase/report discipline | Done | One local collection/import phase, evidence and handoff |
| 2, 6 — data integrity/universe | Done | Real supplied selection; synthetic tests isolated; no invented observations |
| 7, 8 — provider/hygiene | Done | Original rows/receipts, explicit gaps/rejects, no basis upgrade |
| 14 — persistence | Done | New immutable vintages; existing history and evidence retained |
| 15 — scheduling | Not in this phase | No worker, schedule or opt-in change |
| 17, 18 — HTTP/UI | Done | Existing read-only source inspection checked; no UI implementation change |
| 20 — tests | Done | Affected importer/setup/tamper tests and final collector tests executed |
| 26 — provenance | Done | Bounded selected data, source hashes, original receipts and S1 validation |
| 27–31 — strategy/financial/validation | Not in this phase | No new rules, funding, replay, study or promotion |
| 32 — source limitations | Done | Original unknowns retained; richer adapter/calendar remain pending |
| 38 — local source reuse | Done | Separate cache/raw collections, exact benchmark and reconciled import |

## STATE SUMMARY

```text
Completed: user-authorized Finding Multibagger NSE local-data collection/import.
scripts/swing_reuse_local.py: bounded read-only collector and exact CSV mappings.
tests/fixtures/swing_local_source.py: SYNTHETIC index/equity/source fixtures.
tests/test_swing_local_source.py:21 parser/selection/integrity/import cases.
docs/SWING_LOCAL_SOURCE_CONTRACT.md: explicit scope/products and unchanged gates.
docs/SWING_LOCAL_SOURCE_REUSE.md: results, commands, tests, limitations and handoff.
docs/swing/LOCAL_SOURCE_REUSE_REAL_CHECK.json:cache import/hash/HTTP evidence.
docs/swing/LOCAL_SOURCE_RAW_REAL_CHECK.json:raw import/hash/HTTP evidence.
docs/swing/LOCAL_SOURCE_COVERAGE_DELTA.json:common-cutoff comparison/per-symbol gaps.
docs/SPEC.md:section38; README.md:local source reuse reference.
data/swing.db:two new S1 vintages; all three originals retained unchanged.
data/swing/local_sources/20261023/:cache staging/manifest/index receipts (ignored).
data/swing/local_sources/20261023-raw-final/:raw staging/manifest/source receipts (ignored).
data/swing/snapshots/:new immutable JSONL evidence; ignored logs helper/results.
Public collect(source:Path,output:Path,cfg:Settings,clock,*,equity_source='cache')->dict.
index_bar(body:bytes,report_day:str,benchmark:str)->dict.
equity_rows(body:bytes,report_day:str,members)->tuple[list[dict],list[dict]].
fingerprints(path:Path)->dict; CLI requires explicit --source and new --output.
CLI --equity-source cache|nse_bhavcopy defaults cache; raw product NSE_CM_UDIFF_F_0000.
Config keys/dependencies/application schema changes:none.
Cache vintage:e33f2e7f95d69b2702d727661552606287c28e6c16ffb73dcedf4387b3a72283.
Raw vintage:4d8e48f8d01212ae49f07eb7a5940b39f6d8bdc5aadbcd9d0505fc805065b6e4.
Raw stock coverage:497 symbols,191770 session pairs,7030 gaps through22Oct2026.
457 stocks miss only22Oct;40 have older gaps too. Raw stock data ends21Oct.
Benchmark:398 accepted OHLC rows; missing21/22Oct; reports end19Oct.
Canonical basis/identity/publication remain unverified; no tradable plans claimed.
Flags unchanged:false swing/replay; no workers/downloads/financial mutations.
Tests:100 affected passes;21 final product-selection passes; full suite not rerun.
Source/old-vintage/protected hashes equal; repeat IDs reused; HTTP200;quick_check ok.
Next:await CONTINUE;reread SPEC;obtain missing sessions and implement evidence-aware adapter separately.
```

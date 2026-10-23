# NSE archive gap filling — 23 October 2026

Completed one user-authorized phase: use NSE bhavcopy to fill missing data in the
existing separate NSE raw-source inspection collection. This extends the prior
local-only collection phase with explicit archive acquisition. SPEC was reread;
plan was to identify missing document dates, acquire only those documents,
validate compatible rows, append a new vintage and reconcile gaps/old records.
No input clarification blocker remained. All four requested documents returned
HTTP 200 using ordinary public archive GETs.

## Result at the same completed-session cutoff

Cutoff: 22 October 2026, with the existing 400-session requirement and supplied
497-stock selection. No calendar, lookback, strategy or evidence gate changed.

| Check | Prior raw-source snapshot | Gap-filled snapshot |
|---|---:|---:|
| Stocks with observations | 497 | 497 |
| Complete 400-session stock histories | 0 | **457** |
| Stock/session observations | 191,770 | **192,267** |
| Missing stock/session pairs | 7,030 | **6,533** |
| Nifty 500 OHLC/open rows | 398 | **400** |
| Missing required Nifty 500 sessions | 21 and 22 October | **None** |
| Complete 25-session delivery histories | 0 | **493** |
| Accepted delivery observations | 11,854 | **12,349** |
| Canonical signal eligibility | 0 | 0; S1 evidence limitations retained |

All 497 stocks now have the 22 October equity observation. The 40 stocks with
older gaps remain listed by date in the real-check JSON. No values were invented,
forward-filled, copied before listing or taken from Yahoo-adjusted histories.
The two 22 October security-report rows for HFCL.NS and STLTECH.NS contain `-`
in required delivery fields. Their parse failures retain CSV record locators and
the receipt hash. No delivered quantity or 100% BE assumption was invented.

Added: **497 equity + 2 index + 495 delivery = 994 observations**. S1 accepted
192,667 prices (192,267 equity plus 400 index) and 12,349 contexts. Existing
477 NON_SESSION rejections remain visible. Reconciliation:
`192667 + 12349 + 477 = 205493` staged rows. Older gaps are not automatically
recoverable by repeatedly downloading a daily file already present in the source.
Possible listing/identity/trading-history causes need independent evidence;
this phase does not classify every older gap as a pre-listing period.

New vintage:
`98b3a73a42aec99d6a3d54536263221630b6a690510d93a0a799d185824c747a`.
Parent vintage:
`4d8e48f8d01212ae49f07eb7a5940b39f6d8bdc5aadbcd9d0505fc805065b6e4`.
Prepared collection: `data/swing/local_sources/20261023-nse-gap-filled/`.

## Actual acquisitions and timestamp semantics

Only these four document dates/products were requested, once each, with no retry
or redirect following:

- [22 October final NSE CM UDiFF bhavcopy](https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20261022_F_0000.csv.zip):
  204,286 bytes, SHA-256 `4e1e097210a023fa6a2bfd67610528d28ac0307c6417c8c85cfa4b01196d37b2`.
- [21 October index report](https://nsearchives.nseindia.com/content/indices/ind_close_all_21102026.csv):
  17,833 bytes, SHA-256 `ccaff4c523834856afae73d2072d45b62bd766ed6766057534eaaf3f9e7d420c`.
- [22 October index report](https://nsearchives.nseindia.com/content/indices/ind_close_all_22102026.csv):
  17,786 bytes, SHA-256 `869e842e64deacb77ab47733367a1b77c4acfed5a91616dff9310f03fc4ddb51`.
- [22 October security/delivery report](https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_22102026.csv):
  396,002 bytes, SHA-256 `ed7f4f4347f5a9c07580356f6907aad630f34ff7715b69e7b530c6aa7c1f66cb`.

The general web reader could not open these ZIP/CSV URLs. Direct project-venv
httpx GETs succeeded and original response bytes/statuses/hashes were retained.
No alternate host, account, credential, challenge bypass or source-app write was
used. Initial one-off acquisition metadata recorded request-start instants under
an observation label. Before application, those exact instants were retained as
`request_started_at`; `observed_at` was conservatively set to the subsequent local
verification time, with that meaning explicitly recorded. It never claims earlier
historic availability. The reusable fetcher records response observation after
the GET. Publication time stays unknown. Canonical S1 availability remains its
new local import time.

Original acquisition diagnostics live under ignored
`logs/nse-gap-fetch-20261023/`; copied content-addressed receipts and a parent-linked
manifest are retained with the new staging collection. Existing source receipts
remain in the parent collection and are referenced by its manifest hash. Only
allowlisted public price/delivery columns are cloned; no account/application
tables or whole sibling database were copied.

## Reusable explicit workflow

`scripts/swing_fill_gaps.py` exposes:

```text
python -m scripts.swing_fill_gaps plan --base <raw-collection>
python -m scripts.swing_fill_gaps fetch --base <raw-collection> --output <new-receipt-directory> --max-documents N
python -m scripts.swing_fill_gaps apply --base <raw-collection> --receipts <attempts.json> --output <new-collection>
python -m scripts.swing_data import --source <new-collection>/public.db --report <coverage-output.json>
```

These are explicit CLI operations; no timer, API write route, UI button or
automatic provider fallback was added. `fetch` requires an operator-supplied
request limit and uses existing SWING_SOURCE_TIMEOUT_SEC. It records failed HTTP
statuses/errors and follows no redirects. `apply` requires successful receipts;
invalid supplied receipts fail before a collection manifest is published. Missing
unsupplied documents remain explicit. No hidden per-stock download loop exists.

Planning uses exact completed sessions and existing document dates. A whole-market
file already collected is not requested again just because a selected stock is
absent from it. Delivery uses the configured delivery window independently.
Current universe and benchmark must match the parent. Provider-cache collections
are refused; this workflow only extends a declared raw NSE UDiFF collection.

Application verifies parent database/manifest hashes before and after, checks
canonical NSE URL/product/day, receipt hash and aware observation time, validates
daily-file dates (including non-selected rows), and reuses the existing equity/
index mappings. Delivery uses observed volume/quantity/percentage; existing S1
validates its arithmetic. Missing rows are added; unequal overlaps fail. Existing
values are never updated. Unsupported or ambiguous observations retain rejects.
The resulting prepared source is imported via unchanged S1 validation into a new
immutable snapshot, with current local availability and unknown canonical basis.

## Commands executed and verification

Graphify reused the existing map with terms `coverage archive source snapshot
import prices`, DFS and a 600-token budget. Its truncated traversal was navigation
only; it is not a fresh runtime graph of the new CLI. No extraction/token-savings
claim. CodeGraph supplied current source and callers for the existing collectors,
importer and new workflow. SPEC/contracts and stored gap evidence were read.

Executed the read-only plan CLI against
`data/swing/local_sources/20261023-raw-final`; it returned exactly four documents.
Performed the four actual GETs above in bounded venv helpers. No repeat download
was needed to exercise the reusable fetcher; its status/redirect/limit behavior
was tested with MockTransport. Executed:

```text
.venv/Scripts/python.exe -m pytest tests/test_nse_gap_fill.py tests/test_swing_local_source.py tests/test_swing_data.py tests/test_swing_verification.py -q -p no:cacheprovider --basetemp logs/nse-gap-tests/pytest --tb=short -o faulthandler_timeout=60
.venv/Scripts/python.exe -u logs/fill_nse_real.py
git diff --check
```

Tests used isolated workspace TEMP/TMP and synthetic fixtures only. Actual result:
**89 passed in 83.44s (0:01:23)**, exit 0. Fourteen new tests cover document
deduplication, no invented TEST2 rows, complete-session coverage after filling,
old-record preservation, repeated-import ID reuse, retained unknown basis, invalid
hash/URL/status/naive/future/duplicate/date receipts, unequal overlaps, adjusted or
tampered parent refusal, delivery contradiction rejection, and bounded fetch
status/redirect/observation timing. Existing import/local-source/tamper tests also
passed. Full suite was not rerun for this CLI/data-only change.

The real helper reused captured response bytes, applied the new collection,
imported it, reimported it to establish idempotence, verified all six vintages'
artifacts/universes, compared pre-existing logical row hashes across snapshots,
reports, prices, contexts and rejects, and independently checked counts/old price
values/added keys. The 499 new price keys match 497 stocks and two index sessions.
It returned `quick_check=ok`. Protected before/after hashes/absence matched for
history, paper/operations/validation/forward stores and WALs, environment, universe
and settings. The parent manifest/database also matched. The sibling application
and its databases were not opened or changed during this phase.

Actual source inspection via HTTP ASGITransport returned vintage discovery 200
and workspace 200 with the new stored summary. It reported `plans=0`,
`view_state=disabled`, `scheduler_running=false`. No live socket/browser or worker
was started. SWING_ENABLED and SWING_REPLAY_ENABLED remain false. The full
machine-readable result, including exact remaining dates, counters and protection
hashes, is `docs/swing/NSE_GAP_FILL_REAL_CHECK.json`; run output is
`logs/nse-gap-fill-real-run.txt`.

## Assumptions and VERIFIED / NOT VERIFIED

Assumptions: continue the supplied current universe, raw NSE product, existing
calendar, 400/25-session requirements and existing evidence gates. Current source
availability is established from now onward, not at historic signal dates.

VERIFIED: four successful NSE responses and retained bytes; 994 added observations;
497 stocks with the cutoff-session price; 457 complete stock histories; 400/400
benchmark OHLC/open coverage; 493 complete delivery histories; independent counts,
unchanged old values/vintages/parent/protected stores; repeat import IDs; HTTP
inspection; quick check; executed tests; whitespace check (only CRLF notices).

NOT VERIFIED/remaining: 6,533 older stock/session gaps across 40 symbols and four
incomplete delivery histories; causes and date-valid identity/listing/action
repairs; an evidence-aware adapter for raw basis and required context; historic
publication/membership proof, corrected special-session policy, strategy
validation/promotion or profitability. Canonical S1 basis remains unknown, so
coverage completeness alone does not make Trade Ideas signal-ready. No history
was rewritten, Yahoo-adjusted data spliced into NSE rows, or worker/flag enabled.

## Files created or modified

Created: `scripts/swing_fill_gaps.py` (manual planner/fetcher/applier),
`tests/fixtures/nse_gap_fill.py` (labelled synthetic archive/gap fixtures),
`tests/test_nse_gap_fill.py` (14 tests), `docs/NSE_GAP_FILL_CONTRACT.md`, this report,
and `docs/swing/NSE_GAP_FILL_REAL_CHECK.json` (actual import/protection/gap evidence).
Modified: `docs/SPEC.md` section 39 and README gap-fill status/reference.
Runtime: a new vintage in `data/swing.db`, immutable JSONL evidence and the new
collection/receipts under ignored `data/swing/`. Ignored venv helper
`logs/fill_nse_real.py`, captured responses and test/run logs retain reproducibility.
No configuration keys, dependencies or application schema migrations added.
All prior uncommitted work retained. No commit/push/deployment was performed.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0, 22 — phase/report discipline | Done | One explicit gap-fill phase; evidence and handoff |
| 2, 6 — integrity/universe | Done | Actual NSE files; synthetic tests isolated; supplied selection retained |
| 5 — configuration | Done | Existing source timeout plus explicit CLI request limit; no hardcoded strategy thresholds |
| 7, 8 — provider/hygiene | Done | Canonical source/date/hash validation and existing S1 rejections |
| 14 — persistence | Done | New immutable vintage; prior values and stores unchanged |
| 15 — scheduling | Not in this phase | No timer, worker or automatic fallback |
| 17, 18 — HTTP/UI | Done | Existing read-only inspection verified; no API/UI implementation change |
| 20 — tests | Done | 89 affected checks and independent real-data reconciliation |
| 26 — provenance | Done | Original receipts, conservative availability and unchanged unknowns |
| 27–31 — strategy/financial/validation | Not in this phase | No strategy, funds, jobs, alerts or promotion |
| 38, 39 — source reuse/gap fill | Done | Compatible raw parent extended with explicit authorized downloads |

## STATE SUMMARY

```text
Completed:NSE bhavcopy/index/delivery gap-fill acquisition and inspection import.
scripts/swing_fill_gaps.py:explicit document plan/fetch/fill workflow.
tests/fixtures/nse_gap_fill.py:SYNTHETIC archive/gap fixtures.
tests/test_nse_gap_fill.py:14 new integrity/coverage/fetch tests.
docs/NSE_GAP_FILL_CONTRACT.md:scope and invariants.
docs/NSE_GAP_FILL_IMPLEMENTATION.md:results,commands,verification,handoff.
docs/swing/NSE_GAP_FILL_REAL_CHECK.json:real counts/protection/remaining dates.
docs/SPEC.md:section39;README.md:latest gap-fill reference.
data/swing.db:new immutable S1 vintage;five prior vintages preserved.
data/swing/local_sources/20261023-nse-gap-filled/:public staging,manifest,receipts.
data/swing/snapshots/:new hashed JSONL;ignored logs helpers/acquisitions/results.
Public plan(base,cfg,clock)->dict;archive_url(kind,session)->str.
fetch(documents,output,clock,*,client,max_documents:int)->list[dict].
delivery_rows(body,session,members)->(rows,rejects).
fill(base,attempts,output,cfg,clock)->dict;CLI plan|fetch|apply.
Fetch requires explicit request limit;uses existing source timeout;no redirects/retries.
Config/dependency/application schema changes:none.
New vintage:98b3a73a42aec99d6a3d54536263221630b6a690510d93a0a799d185824c747a.
Acquired4officialNSE files;added497equity+2index+495delivery observations.
457of497stocks complete400sessions;benchmark400of400;delivery493complete25sessions.
Remaining6533stock/session gaps across40symbols;four incomplete delivery histories.
Two22Octdelivery report rows contain '-' and remain explicit parse rejects.
All old price values/vintages,parent files and protected stores unchanged.
Canonical basis/identity/publication remain unverified;no signal eligibility claim.
Flags remain false;scheduler stopped;no automatic jobs or financial mutations.
Tests89passed in83.44s;repeat import reused ID;HTTP200;quick_check ok;diff check passed.
Next:await CONTINUE;reread SPEC;address remaining identity/history/context evidence separately.
```

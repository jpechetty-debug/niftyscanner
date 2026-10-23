# Local source reuse — inspection import

23 October 2026. User supplied `D:/Codex/Finding Multibagger NSE` as the local
market-data source. One collection/import phase, not the separately proposed
evidence-backed provider or calendar implementation.

Read `multibagger634-main/lite.db` using SQLite mode=ro, query_only and a consistent
read transaction. Select only current supplied universe price/delivery columns,
with the existing configured trailing windows; retain malformed/future cache
rows for normal S1 rejection. Never copy account/credential/application tables
or invoke the source project's runtime. Keep original stores and old vintages.

Find the exact `Nifty 500` row in source documents identified by
`nse-index-close-observed-v1`. Preserve its reported OHLC/volume, source document
ID/URI/hash, source ingestion/publication metadata and original CSV bytes. Require
a supported NSE archive URI, matching document/row session, available ingestion,
unique target row, valid finite OHLC and consistent duplicate revisions. Missing
index volume stays null. Do not substitute Nifty 50, another index or TRI.

Keep provider-cache equity collection and `nse-bhavcopy-observed-v1` raw equity
collection in separate snapshots. Raw mode selects observed EQ/BE rows for the
supplied universe from original daily bhavcopies; it never fills raw gaps with
provider cache rows. Preserve original bhavcopy bytes and metadata, reject
duplicate document revisions for review, and record per-row invalid/ambiguous
traded-series rejects. Support the observed legacy and UDiFF column mappings.
Raw collection explicitly selects the final `NSE_CM_UDIFF_F_0000` cash-market
product by its date-matched URI. The same source ID also contains security/
delivery reports; list those as excluded products, not competing revisions of
the chosen product. Multiple revisions of the chosen product still fail.
The S1 canonical adapter still records unknown basis: these richer receipts are
inputs for later evidence-backed adapter work, not an automatic attestation.

Observed CSV mapping: `Index Name`, `Index Date` (DD-MM-YYYY), `Open Index Value`,
`High Index Value`, `Low Index Value`, `Closing Index Value`, optional `Volume`.
Map exactly Nifty 500 to configured `^CRSLDX`; custom mappings require a new review.

Publish only into a new staging directory, with public.db, a manifest and
content-addressed index receipts. Bookend source database and nonempty WAL
hashes; abort manifest publication if they change. SQLite's empty WAL contains
no committed frames and is treated as absent; no immutable/nolock bypass is used.
SHM is transient lock/index metadata, not a source of market observations.

Explicitly import the collected public cache through existing S1 validation.
This preserves unknown price basis/identity/publication, exact-date failures,
calendar policy, existing gates and historical evidence. Record independent row
and gap reconciliation before claiming improved coverage. Coverage improvement
is not signal eligibility. No source downloads, opt-in flag edits, workers,
accounts, trades, prospective journals or promotion are part of this phase.

# Explicit NSE archive gap filling

23 October 2026. User authorized using NSE bhavcopy to fill missing data.
This phase extends local collection with explicit missing-document acquisition
and a new inspection vintage; the previous local-only phase remains unchanged.

Plan completed-session document gaps in an explicitly selected raw NSE collection.
Require unchanged supplied universe and benchmark. Compare equity/index receipt
dates to configured exact trailing sessions and delivery dates to the configured
delivery window. Do not redownload an existing whole-market file for every
missing stock row: absent listing/traded observations remain missing.

Acquire only planned official NSE final UDiFF cash-market files, exact-index CSVs
and security/delivery CSVs. Explicit fetch has an operator-supplied request limit,
existing configured source timeout, no redirects or hidden retries. Preserve HTTP
status, URL, actual retrieval time, original bytes and SHA-256. Unavailable files
remain unavailable; a ZIP/CSV extension alone does not prove a valid document.

Apply only verified, date-matched, completed-session receipts to a new staging
directory. Base collection must be raw NSE mode, have matching manifest/staging
hashes and supplied universe, and retain original source receipts via parent
references. Only allowlisted public columns are copied. Add missing rows; an
overlapping unequal value or ambiguous revision fails without overwriting it.
Validate exact Nifty 500 identity and observed OHLCV. Delivery quantity/percentage
come from the security report, never inferred from traded volume or BE series.
Record per-row rejects. Freeze parent manifest/hash and new acquisition receipts.

Publish through existing S1 validation into a separate immutable vintage. Old
records/sources/financial stores remain unchanged. All canonical S1 basis,
identity, publication and eligibility limitations remain unchanged. No calendar
or strategy change, automatic job, opt-in enabling, source-app write, funding,
alert, broker action, historical timestamp backfill or promotion is authorized.

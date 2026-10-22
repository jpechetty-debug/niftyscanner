# Proposed swing source-bundle contract

Date: 22 October 2026. P0 design deliverable; not an implemented importer/API.
This specifies what the next local import phase must prove. Exact publisher-file
column mappings require inspection of actual supplied files before implementation.
Existing v1 adapters and historical evidence keep their current interpretation.

## Purpose and boundary

Support explicit local input bundles that carry prices plus evidence of their
meaning and availability. A new schema/provider capability must be distinguishable
from `multibagger-public-cache-v1`; never grant v1 stronger attestations because
new code or a header claims they exist.

The input may combine multiple explicitly declared publisher artifacts in one
frozen bundle, with item-level provenance. That is different from silently
stitching historical database vintages during a request. Each read continues to
require one explicit bundle vintage, a known requested basis and an aware as-of.

No hidden downloads, credentials, broker/account records or source runtime
imports. No automatic scheduling, paper jobs, portfolio funding or promotion.
No dependency/configuration change is authorized by this design document alone.

## Required logical records

These are canonical contract fields, not claims about an NSE file's actual column
names. Keep original source bytes and freeze a tested mapping for every format.

| Record | Required content and meaning |
|---|---|
| Bundle manifest | Schema/adapter version, dataset ID/revision, created/observed/imported timestamps, source IDs, file paths/hashes, explicit selected universe, benchmark identity, calendar-policy version/hash, transformation/version hashes |
| Source receipt | Publisher, artifact identity/URL or local origin, actual retrieval time, SHA-256, source revision/finality status, supported publication timestamp and evidence if available |
| Equity observation | Exchange, date-valid security ID, symbol, series, ISIN, session, full OHLC, observed volume, INR, explicit price basis, source receipt and record locator |
| Identity interval | Security ID, symbol/series/ISIN, effective-from/to, publication/availability evidence, explicit identity transition links |
| Corporate-action/context record | Security, event type, announcement/effective dates, original event terms, source/availability evidence, supported treatment or explicit unsupported state |
| Index observation | Exact Nifty 500 price-index identity, session, observed open/high/low/close where supplied, price-index basis and source receipt; absent volume remains null |
| Dated research context | Kind/ticker/session, sector/earnings/surveillance values, available/unavailable/stale/conflicting/not-applicable status, publisher/ingestion/publication evidence |
| Delivery observation | Observed volume, delivered quantity, reported percentage, precision, source, security/series/date and availability; contradictory arithmetic remains a rejection |
| Historical universe interval | Authoritative index/security identity, membership effective-from/to and announcement/availability evidence; required for historical membership claims |
| Calendar policy | Session date, sourced open/close bounds, normal/special/closed classification, circular reference and policy version; no global cached-calendar mutation |

Bundle hashes prove stable content, not publisher authenticity or historical
availability. A verified flag must be derived from supported receipt evidence,
not accepted directly from a user-edited JSON boolean.

## Availability and evidence states

- Every instant is timezone-aware. Retain source publication time separately
  from actual retrieval and local ingestion.
- `available_at` must be no earlier than actual local ingestion and supported
  publication. Missing publication evidence remains unknown. Never derive it
  from a session date or an end-of-day label alone.
- Downloading historical data now cannot make it available to last year's
  simulated decision. New revisions cannot replace the original decision inputs.
- Current supplied membership is sufficient only to describe the current
  selected universe. It does not establish historical index membership.
- Distinguish inspection coverage, calculable research inputs, executable paper
  evidence and historical validation readiness. An output must state exactly
  which level is supported and which gates remain missing.
- For prospective research, newly observed data can establish availability from
  now onward. This does not repair historical publication claims or override
  existing v1 gating. Any new prospective evidence policy must be explicitly
  specified and tested before implementation.

## Price and identity rules

The frozen v1 strategies request raw trading prices. Provider-adjusted unknown,
split-adjusted, cash-dividend-adjusted and total-return observations must not be
silently relabelled raw. Preserve those bases separately if present.

Validate finite positive OHLC, OHLC ordering and positive equity volume;
validate exact instrument/security identity and session uniqueness. Use date-valid
series/ISIN evidence rather than today's constituent metadata. Explicitly reject
unsupported renames/series/action transitions instead of fabricating continuity.

Keep action evidence separate from observed prices. Unsupported material actions
must remain an unavailable/reconciliation state for any affected calculation or
execution. Price adjustment or reference-price rebasing requires an explicit
policy and transformation hash; this contract does not define a new formula.

Missing sessions are never forward-filled. Newly listed securities with insufficient
warm-up stay unavailable; do not require them to have traded before listing.
Unavailable benchmark opens are not estimated from closes. An ETF or Nifty 50
cannot stand in for the configured Nifty 500 price index.

## Session policy

Version the calendar with the source bundle. Include confirmed special-session
bounds; initially review 21 October 2025 using
[NSE/CMTR/70319](https://nsearchives.nseindia.com/content/circulars/CMTR70319.pdf).
Unknown special sessions remain unsupported until evidenced. Do not assume
that this single circular establishes all historical NSE/BSE sessions.

Recalculate exact trailing sessions under the new policy, preserving previous
vintage schedules and all existing resolved cohort references. A policy change
creates a new vintage and visible coverage delta. No four-hour aggregation is
introduced by the daily source importer.

## Import, read and delta requirements

1. Require explicit input/output paths. Resolve them and reject overlap with
   source files, existing immutable artifacts, protected stores or output reports.
2. Open sources read-only; retain only the approved market-data/evidence inputs.
   Do not copy an entire sibling application database into the project.
3. Validate the manifest, hashes, mapping, provenance and rows before publication.
   Missing evidence is an explicit state, never an implicit default success.
4. Commit canonical observations, rejects and the resulting readiness report
   atomically in a versioned store. Preserve original artifacts; no overwrite.
5. Identical input content plus mapping/calendar policy is idempotent. Changed
   source revision, mapping or policy creates a distinct vintage.
6. Reads require exact requested sessions and as-of availability from one declared
   vintage. No fallback to earlier bars, different bases or unrelated indexes.
7. A coverage delta names both vintages and their policies. Report unchanged,
   added, revised, rejected and unresolved records; list per-symbol missing dates
   and identify policy-induced differences separately from new source observations.
8. Show first blocking prerequisite and remedy without losing other failures.
   A source blocker is distinct from a normal setup-rule rejection. Data repair
   does not add or remove a screening condition.

## Required acceptance tests for the next phase

Use labelled SYNTHETIC fixtures under `tests/fixtures/` only. Real-data checks use
actual supplied artifacts and are never replaced with test rows in production.

- Old v1 unknown price basis remains unknown after migration/new adapter support.
- A real receipt locator and supported attestation are required to derive proof;
  edited header booleans cannot make unknown evidence valid.
- Missing/naive/future timestamps, future observations and as-of-unavailable
  revisions fail appropriately. Local import time is not historic publication.
- Duplicate/invalid/non-session bars and contradictory delivery retain exact
  reasons. Short listing histories are not backfilled.
- Verified special-session bounds produce a separate policy/vintage without
  mutating the global XBOM calendar or existing resolved schedules.
- Missing/wrong benchmark or unknown index basis cannot pass via substitution.
- Reimport is idempotent; interrupted validation/import rolls back; changed
  content does not overwrite the previous vintage.
- Coverage deltas reconcile added/revised/rejected/date-policy changes and their
  totals against independently computed observations.
- Source/protected stores remain unchanged; unrelated source/account tables are
  never exported; invalid output-path overlaps are rejected.
- API/UI exposure preserves the synthetic-data prohibition, read-only calculation
  semantics and existing filtering/scoring/ledger behavior.

## Inputs still needed

Representative real equity and Nifty 500 files, identity/action evidence and dated
context are needed before freezing publisher-column mappings. Historical index
membership/publication evidence is separately needed for historical validation.
No working authenticated provider or complete source bundle has been supplied
or established by P0. Buying/subscribing is not assumed.

Source categories inspected: [NSE reports](https://www.nseindia.com/all-reports),
[index historical reports](https://www.niftyindices.com/reports/historical-data),
[index constituent data products](https://www.niftyindices.com/offerings/data-subscription).
Their existence does not verify full target-window coverage for this project.

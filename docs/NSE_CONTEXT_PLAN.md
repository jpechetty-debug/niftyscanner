# NSE context layer: stage after signal outcome tracking

Date: 4 October 2026. User-requested roadmap stage; not implemented by this audit.
Prerequisite: the outcome storage, timing contract and Performance reporting in
`docs/SIGNAL_OUTCOME_PLAN.md` must be working before adding this feature stage.

## Order of delivery

1. Outcome tracking: establish 1/5/10-session baseline performance and coverage.
2. Context layer: collect delivery percentage, relative strength and industry
   classification as visible columns, optional view filters and recorded factors.
3. Evaluation: compare contextual subsets with the baseline on equivalent entry
   dates, horizons, strategy versions and available data; reserve a later period
   for checking findings rather than choosing and testing thresholds on one set.
4. Only then introduce explicitly versioned/configured score weights or screening
   changes. Keep the original strategy available as a comparison baseline.

No new scoring thresholds or weights are chosen in this plan. A classification
is categorical, not an ordinal numeric score. The first concentration feature
is a warning; any diversification selection/penalty needs a separately defined
and evaluated rule. Delivery activity alone does not establish net accumulation
or institutional buying.

## Source audit and integration

NSE's official report listing distinguishes:

- CM - Security-wise Delivery Positions.
- Full Bhavcopy and Security Deliverable data.
- CM-UDiFF Common Bhavcopy Final (zip).

It also marks the old CM/Common Bhavcopy CSV formats as discontinued from
8 July 2024. Do not assume every current UDiFF bhavcopy contains delivery fields
or use an old filename template as a permanent provider contract.

The current local constituent CSV has these headers:
`Company Name,Industry,Symbol,Series,ISIN Code`. The loader currently retains
only symbol/company name and constructs the Yahoo ticker. Preserve optional
Industry/Series/ISIN metadata without making it required for existing universe
files or assigning classifications from memory.

Create an isolated `NseContextProvider` protocol/implementation separate from
the yfinance price/P/E provider. Endpoint discovery/configuration, download,
archive handling, dated format adapters, validation and caching belong there.
Use its own injected Clock, bounded retries, circuit breaker and logging.

Fetch/cache each complete exchange report once per date, not once per ticker.
Retain raw-file hash, report date, source URL, retrieval/first-observed timestamp,
schema version and normalized data. Validate report date and required columns,
numeric bounds, identifier uniqueness, content type and body. Block/HTML pages
must never be accepted as CSV. For ZIP reports enforce safe extraction and size
limits. Unsupported schemas remain unavailable with a clear provider issue.

Transport/rate-limit failures affect this provider's breaker. A missing ticker
does not. A report not yet published is pending with bounded retries; do not
confuse that with a zero delivery percentage or break the screening provider.
Persistent incompatible file formats require visible diagnostics and no silent
fallback to stale same-day-looking data.

This explicitly requested NSE enrichment extends the previous yfinance-only
source scope for context. Document that source-specific extension in SPEC during
implementation; the core price/P/E provider and its contract remain intact.

## Delivery percentage and time availability

Normalize report values as delivery percentage = 100 × deliverable quantity /
traded quantity. Prefer validated official reported percentage when supplied;
cross-check its definition and rounding against quantities. Use the report's
traded quantity, not yfinance's adjusted/projected volume. Zero/absent denominator
means unavailable, not 0%. Join by date and symbol/series, using ISIN to validate
identity where available; never merge different trading series silently.

Same-session delivery data is usable only after that dated report is observed
and validated. Post-close enrichment may finish later than the scan. Missing
current delivery stays pending/unavailable. An intraday screen may show the
previous session's delivery explicitly dated, but must not treat it as today's
delivery or apply a same-day filter against it.

Store factor date and availability separately from signal bar date. Never
backdate an archive downloaded today to an assumed historical publication time.
Distinguish retrospectively researched context from prospectively captured
context. Do not mutate the original historical signal's score when enrichment
arrives; create a timestamped context snapshot or strategy revision.

For an enriched signal, the outcome tracker uses an entry no earlier than the
context became available. Compare baseline and enriched selections at a common
entry time to avoid crediting delivery information that was unavailable to the
baseline. Keep missing context out of numeric scoring while reporting coverage.

## Relative strength against Nifty 500

Display two lookbacks, proposed as configurable 21 and 63 completed trading
sessions (approximately one and three months). Label them by session count.
Use the same verified price-return basis as outcome tracking for stock and index.

```text
RS(h), percentage points = 100 × [(stock_t / stock_(t-h) - 1)
                                - (index_t / index_(t-h) - 1)]
```

RS here means trailing excess return, not Wilder RSI or a cross-sectional
percentile. Use matching endpoint dates and an as-of completed session; no future
close in intraday views. Sixty-three sessions require sixty-four valid endpoint
observations in the reference series. Insufficient history, a missing required
quote or benchmark gap yields unavailable with a reason; do not shorten the
window or substitute a different index. Share the validated historical-price
cache/benchmark identity from stage 1, and keep adjustment vintages consistent.

## Sector/industry and concentration

Use the CSV's provided Industry classification initially and label it honestly
as Industry. A true Sector field requires a verified, versioned taxonomy mapping;
do not invent one or call an industry a different classification without one.
Capture source/version and observation date with each signal. Current metadata
cannot be represented as point-in-time historical classification.

Show group counts and shares of the complete qualified scan, plus a configurable
concentration warning above the user's limit. If view filters change the subset,
show its separate counts and denominator. Include Unknown classification and
metadata coverage; do not drop unknown names to improve the concentration ratio.
This describes concentration of picks, not actual portfolio weights.

## Optional F&O extension

Add a nullable F&O eligibility flag only if the user chooses the F&O workflow.
Use NSE's dated underlying/contract master or official eligibility list. Preserve
effective dates and source so today's eligibility is not assumed historically.
Unknown is different from ineligible; eligibility is different from ban/restricted
status. Display and optionally filter without assuming execution availability.

Open-interest change is deferred. It needs contract/expiry identification,
futures versus options scope, rollover policy, units and timestamps. Do not sum
arbitrary expiries or label an underlying's aggregate as a specific contract's
change. Adding it requires its own later reviewed measurement contract.

## API/UI and testing scope

Expose nullable context values, dates, availability reasons and provenance
through HTTP API payloads. Add optional filters and compact columns for Delivery
%, RS 21s, RS 63s and Industry/Sector; an F&O column is conditional. Unknown
context must remain visible. Backend scoring changes are disabled until stage 3;
view filters narrow results and do not silently alter the recorded base strategy.

Capture factors for the complete scanned universe where possible, including
rejected stocks/decision outcomes, if future threshold research is intended.
Qualified-only records cannot support general threshold optimization. Retain
original strategy configuration and version for cohort comparisons.

Acceptance tests: dated format adapters and malformed/block responses; delayed
publication and holidays; date/series/ISIN joins; zero denominators and percentage
bounds; same-day versus previous-day context; no retrospective availability;
21/63-session endpoints, corporate actions and benchmark gaps; optional universe
columns/unknown taxonomy; concentration denominator and boundary behavior; F&O
effective dates; independent provider breakers; persistence, API and UI unknown
states; enrichment-to-outcome entry alignment. Synthetic inputs stay in tests.

## Verification and commands

VERIFIED: official report names/deprecation notice; existing CSV headers and
loader behavior. NOT VERIFIED: downloadable report bytes/schema, exact report
publication times, live context values, historical classification/F&O coverage,
or improvement in measured signal outcomes. No fetcher, factors or scores were
implemented and no live scans/production data modifications occurred.

Read SPEC; inspect CSV header; explore universe/provider interfaces with
CodeGraph; browse the official references below; execute:

```powershell
$taskContextAuditTemp = Join-Path (Get-Location).Path ('logs/context-audit-tests-' + [guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe -m pytest tests/test_universe.py -q -p no:cacheprovider --basetemp $taskContextAuditTemp
git diff --check
git status --short
```

Real focused-test output:

```text
...                                                                      [100%]
3 passed in 1.01s
```

The earlier full-suite
outcome audit remains 111 passed, one existing dependency warning; it was not
repeated for this documentation-only roadmap change.

Primary references:

- [NSE all reports](https://www.nseindia.com/all-reports)
- [NSE security-wise archives](https://www.nseindia.com/report-detail/eq_security)
- [NSE securities available for trading](https://www.nseindia.com/static/market-data/securities-available-for-trading)
- [NSE UDiFF formats](https://www.nseindia.in/static/resources/forms-formats-members)

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0 — Phase discipline | Done | Roadmap only; no second implementation phase started. |
| 2 — Data integrity | Done | No classifications, delivery data or outcomes fabricated. |
| 3, 7, 8 — Provider/data | Not in this phase | User's NSE context extension isolated and documented. |
| 5, 6, 9, 12 — Config/universe/indicators/ranking | Not in this phase | Context capture precedes any measured scoring change. |
| 14, 15 — Persistence/scheduler | Not in this phase | Availability snapshots and post-close integration planned. |
| 17, 18 — API/UI | Not in this phase | Nullable dated context and optional filters scoped. |
| 20 — Tests | Done | Existing universe tests executed; no application edits. |
| 22 — Reporting | Done | Evidence, assumptions and handoff recorded. |

## STATE SUMMARY

```text
Completed: NSE context roadmap audit; stage 1 remains signal outcomes.
Created docs/NSE_CONTEXT_PLAN.md: provider, factors, timing and evaluation scope.
Updated docs/SIGNAL_OUTCOME_PLAN.md: reference to the second feature stage.
Updated docs/FEATURE_IMPROVEMENT_PLAN.md: ordered feature roadmap.
Application/database changes: none.
Public functions/classes and config keys added: none.
Decision: display/capture context first; score changes follow outcome evaluation.
Decision: 21/63 completed-session trailing excess returns against Nifty 500.
Decision: retain Industry honestly; verified taxonomy needed for a Sector mapping.
Decision: optional dated F&O eligibility; open-interest change deferred.
Known limits: source report bytes/timing and actual predictive value unverified.
Tests: 3 universe tests passed in 1.01s; earlier full baseline 111 passed.
Next phase first step: after CONTINUE, implement the outcome plan first.
Stopped: no context implementation begins before completed outcome tracking.
```

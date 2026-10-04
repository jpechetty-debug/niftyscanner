# Review completion phase — October 4, 2026

Implemented the missing review features and corrected effective configuration/documentation. No commit or push was performed in this phase.

## Result

| Item | Current behavior |
|---|---|
| DUMMY/RR exclusions | Already implemented; fresh real scan uses 497 eligible constituents from 501 raw CSV rows |
| BE equities/special symbols | Existing eligibility preserved; regression suite passes |
| Refresh interval | Default, saved preference and running API now 300 seconds |
| January 15 closure | Confirmed NSE holiday override supplements a separate XBOM instance; next-open/session horizons skip it |
| Circuit proximity | Daily move, nullable risk/status/reason fields, table/card warnings, API/CSV/JSON support |
| Trading costs | Performance supports Gross / Estimated net; hit rates and all bucket summaries use selected basis |
| Independent P/E spot-check | Optional dated CSV comparison; rankings/filtering remain Yahoo-based |
| Documentation | Removed unsupported future-holiday completeness and per-session request guarantees; corrected SPEC default/universe contract |

## Circuit and P/E reference contract

Set `NSE_REFERENCE_CSV` to a local CSV containing independently checked reference data. No real reference rows are bundled or fabricated. The header is:

```csv
Symbol,Date,Source,PE,PriceBandPct,FNO
```

- `Symbol`: NSE symbol, optionally ending in `.NS`.
- `Date`: exact signal session date, `YYYY-MM-DD`; stale dates are not reused.
- `Source`: provenance supplied by the user; nonempty source is required before using reference metrics. This label does not prove that the underlying values are authoritative.
- `PE`: optional finite positive trailing P/E. Compare Yahoo P/E using `100 * abs(yahoo_pe / reference_pe - 1)`, tolerance `PE_REFERENCE_TOLERANCE_PCT=25`.
- `PriceBandPct`: optional fixed percentage band, positive and <=100. Require explicit `FNO=no|false|0` before using it.
- `FNO=yes|true|1`: flexible operating range; do not apply the fixed-band inference.

Duplicate symbol/date records, malformed/unreadable files and missing references become unavailable context without changing qualified/filtered/failed counts. The swappable `ReferenceProvider.load()` protocol isolates the input.

Without a matching reference, moves within `CIRCUIT_PROXIMITY_PCT=0.5` percentage points of `CIRCUIT_BANDS_PCT=2,5,10,20` produce an **indicative** warning. Other unknown bands retain `circuit_risk=null`; unknown never means safe. With supplied fixed-band context, moves at/above the band minus tolerance receive a reference warning. Daily move is a Yahoo adjusted-close proxy; corporate actions, delayed partial bars and next-open changes limit execution inference.

[NSE's price-band guidance](https://www.nseindia.com/static/regulations/daily-price-bands-reports) describes fixed bands and flexible operating ranges for derivatives-eligible securities. A proximity flag does not prove that a stock is locked at a circuit or that the next-open trade can execute. The public band CSV timed out in this verification; the NSE quote endpoint returned HTTP 403. Automatic exchange ingestion was therefore not claimed or bypassed.

## Cost model

`GET /api/performance?return_basis=gross|net` defaults to gross. Resolved rows expose gross/net stock and excess returns plus estimated cost drag. Original SQLite gross returns remain unchanged; report calculations use current configured rates uniformly across history. Benchmark returns remain gross price-index returns.

Default basis points (1 bp = 0.01%): buy STT 10, sell STT 10, buy stamp duty 1.5, brokerage 0 per side, other costs 1 per side, slippage 5 per side. [NSE's published levy schedule](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies) supports the delivery-equity STT/stamp rates. Brokerage, other-cost allowance and slippage are configurable assumptions, not exact statutory calculations.

```text
buy_rate = (buy_STT + stamp + brokerage + other) / 10000
sell_rate = (sell_STT + brokerage + other) / 10000
slip = slippage / 10000
net_return_pct = 100 * (exit * (1-slip) * (1-sell_rate)
                       / (entry * (1+slip) * (1+buy_rate)) - 1)
net_excess_pp = net_return_pct - gross_index_return_pct
```

No fixed DP fee, statutory rounding, order-size/liquidity/slippage curve, executable-fill guarantee, dividends or significance test. Five/ten-session outcomes overlap. Pending/unresolved signals never receive invented net returns or wins/losses.

## Calendar

`NSE_EXTRA_HOLIDAYS=2026-01-15` follows [NSE circular 72260](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf). Tests verify January 14 post-close captures enter January 16, while global XBOM and NYSE calendars remain unchanged. The override is shared by API, CLI, scanner, state defaults and live verifier.

Existing resolved cohort schedules remain immutable. This repository's current cohort dates are all October 1, so the January repair does not alter those schedules. Older imported archives spanning the missing holiday need a separate explicit schedule audit. Future calendar completeness and Muhurat special hours remain unverified.

## Files created / modified

- `app/providers/nse_reference.py`: reference provider protocol/CSV reader and risk/P/E enrichment.
- `app/performance/costs.py`: net cash-flow return and cost assumptions.
- `app/core/config.py`, `.env.example`: validated holiday/reference/risk/cost settings.
- `app/market/calendar.py`: confirmed NSE closures without global-calendar mutation.
- `app/core/indicators.py`, `app/core/interfaces.py`: daily move and backward-compatible result fields.
- `app/services/scanner.py`: enrich once per scan; optional injected reference provider.
- `app/main.py`, `app/services/state.py`, `app/cli.py`, `scripts/live_verify.py`: pass configured calendar; offline CLI excludes real reference files.
- `app/api/routes.py`: new CSV export fields.
- `app/performance/service.py`, `app/performance/api.py`: selected gross/net report and validated HTTP query.
- `ui/api_client.py`, `ui/performance.py`, `ui/app.py`: HTTP-only controls, cost disclosures, readable band/P/E status and card cautions.
- `tests/test_missing_features.py`, `tests/test_performance.py`: meaningful regression/integration/UI checks.
- `README.md`, `docs/SPEC.md`, `docs/REAL_DATA_REVIEW.md`, `docs/IMPLEMENTATION_STATUS.md`, this report: updated contracts and honest verification limits; historical audit preserved.
- Local ignored artifacts: `data/settings.json` preference, real `data/last_scan_NSE.json` snapshot/history update, API logs and `logs/feature-verification/` backup/JSON/screenshot evidence.

## Commands executed and actual results

Used CodeGraph before source exploration/edits; read SPEC/AGENTS; ran scoped git status/diff checks and public read-only NSE source probes. Applied local file patches and reloaded only the identity-checked preview API process. Its scheduler remains disabled, matching the existing preview configuration.

```powershell
$taskTmp=Join-Path (Get-Location).Path ('logs/feature-final-tests-'+[guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp $taskTmp
.venv/Scripts/python.exe -m compileall -q app ui scripts
git diff --check
.venv/Scripts/python.exe -m scripts.live_verify --mode market-hours --check-only
.venv/Scripts/python.exe -m scripts.live_verify --mode post-close --check-only
```

Final full-suite output:

```text
208 passed, 1 warning in 66.80s (0:01:06)
```

The warning is an existing Starlette/anyio deprecated `BlockingPortal` alias. Compilation and whitespace checks passed. During development, two added test assumptions were corrected: the schedule tuple's entry index and the synthetic integration universe/provider size mismatch. Final regression tests pass.

Both live verifier modes returned `in_window=False` and deferred without Yahoo requests, because October 4 is Sunday. These are not successful market-window verifications.

HTTP verification executed PUT settings 300; GET net Performance (200); invalid return basis (422); POST one manual real NSE refresh (202); GET refreshed results/CSV. SQLite was backed up using its backup API before the real scan, and its integrity check remains `ok`. Live browser verification selected Performance / Estimated net and expanded the human-readable assumptions without an app exception.

Actual fresh scan (23:34 IST): 497 fetched, 0 failed, RSI filtered 418, volume filtered 45, liquidity filtered 0, Stage 1 survivors 34, P/E filtered 16, qualified 18; 31.69 seconds and 531 provider attempts. Feed session is October 1; Sunday completion is not an October 4 trading session. Effective refresh 300 and stale=false.

Risk context: 2 indicative warnings, 16 unavailable bands, 18 unavailable independent P/E references. SQLite NSE signals increased 54→72 for the first capture with the current explicit strategy context; NYSE remained 39. The report has 18 legacy-unknown and 18 current-strategy cohorts, all pending, so no real hit-rate claim is possible yet. Same-session comparisons can be separated with the strategy selector; legacy captures cannot be proven to share the current thresholds.

## Assumptions and VERIFIED / NOT VERIFIED

**VERIFIED:** dated/duplicate/reference/F&O checks using labelled synthetic fixtures; no reference means unavailable; daily move propagation through scanner/JSON/API/CSV; net grouping can change hit rate; gross database values survive net reports; cost validation; holiday exclusion/next-open dates/global-calendar isolation; UI controls; effective saved/runtime 300; fresh real 497-stock scan; SQLite integrity.

**NOT VERIFIED:** automatic NSE band/P/E access, reference accuracy without a supplied real file, future calendar completeness/Muhurat timing, real market-hours/post-close-window behavior, actual next-open fills, true trading costs and significance/forward profitability. Preview nightly scheduling is intentionally paused; the regular backend enables the implemented scheduler. No commit/push was attempted.

Assumptions: BE trade-for-trade delivery equities remain eligible; risks enrich rather than exclude/rerank stocks; current bps costs apply uniformly to history; supplied reference provenance is user-checked; no unknown band is called safe. No synthetic data was served by the production API/UI.

## SPEC compliance

| Section touched | Status | Evidence / limit |
|---|---|---|
| 0: operating discipline | Done | One authorized completion phase; report, actual commands/results and state below |
| 1/2: source integrity | Done | Real Yahoo scan; optional supplied references; synthetic data confined to tests/offline |
| 3: calendar/stack | Partial | Installed calendar API tested, known closure corrected; special NSE sessions unverified |
| 5/6: config/universe | Done | Default/saved/live 300; valid settings; SPEC now matches DUMMY/RR/BE behavior |
| 7/8/9: providers/indicators | Done | Isolated swappable reference reader; daily move; bounded volume behavior retained |
| 10/11: filtering/scoring | Done | Risk/P/E comparisons do not change screening thresholds or ranks |
| 12–17: persistence/scheduler/API | Done | Stored gross preserved, additive fields, validated net query, successful real refresh |
| 18/20: UI/disclosures/tests | Done | HTTP-only UI, explicit assumptions/limits, 208 passing tests |
| 23: review completion | Partial | Requested warning/cost/closure/config features implemented; automatic exchange reference ingestion remains unavailable |

## STATE SUMMARY

```text
Phase: authorized review completion implemented and verified; stopped.
app/providers/nse_reference.py: ReferenceProvider.load(); CSVReferenceProvider(path).load(); enrich_result(item,config,references).
app/performance/costs.py: cost_model(config); net_return(entry,exit_price,config).
app/core/config.py + .env.example: validated holiday/reference/circuit/cost keys.
app/market/calendar.py: NSECalendarWithOverrides(extra_holidays); MarketCalendar(market,config=None).
app/core/indicators.py: IndicatorResult.day_change_pct; cleaned adjusted-close daily move.
app/core/interfaces.py: nullable risk/source/PE comparison result fields.
app/services/scanner.py: StockScannerService(...,reference_provider=None); enrich once per scan.
app/main.py: API calendars consume configuration.
app/services/state.py: fallback calendars consume configuration.
app/cli.py: configured calendar; no real reference enrichment in offline mode.
scripts/live_verify.py: configured calendar for actual-window verification.
app/api/routes.py: exports additive context fields.
app/performance/service.py: report(...,return_basis='gross'); selected summaries, stored gross preserved.
app/performance/api.py: GET /api/performance?return_basis=gross|net.
ui/api_client.py: get_performance(...,return_basis='gross').
ui/performance.py: gross/net selection, cost disclosure, same bucket/coverage logic.
ui/app.py: daily move, readable risk/PE statuses, card cautions.
tests/test_missing_features.py + tests/test_performance.py: new regression/integration/UI coverage.
README.md: current features and honest limits.
docs/SPEC.md: synchronized defaults, universe contract, authorized extension §23.
docs/REAL_DATA_REVIEW.md: current triage resolutions.
docs/IMPLEMENTATION_STATUS.md: historical audit retained with completion pointer.
docs/FEATURE_COMPLETION.md: phase report and configuration contract.
Ignored data/settings.json + scan/history: refresh 300, real 497-stock scan retained.
Ignored logs/feature-verification/: consistent pre-scan DB backup, API evidence, UI screenshot.
New keys: NSE_EXTRA_HOLIDAYS, NSE_REFERENCE_CSV, CIRCUIT_BANDS_PCT, CIRCUIT_PROXIMITY_PCT.
New keys: PE_REFERENCE_TOLERANCE_PCT; PERFORMANCE_{BUY_STT,SELL_STT,STAMP,BROKERAGE,OTHER_COST,SLIPPAGE}_BPS.
Tests: 208 passed; compilation and whitespace checks passed.
Assumptions: BE eligible; risk is context; costs estimated; supplied provenance user-checked.
Known issues: live reference unavailable; Muhurat/future calendar unverified; no actual fill guarantee.
Preview scheduler remains disabled; historical signals are pending, no observed win-rate yet.
No commit/push in this phase. Next step: user-authorized CONTINUE scope; real market-window verification.
```

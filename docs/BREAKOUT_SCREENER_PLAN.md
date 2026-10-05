# Breakout Stock Screener integration plan

Date: 5 October 2026. Status: planning complete; implementation has not started.

Source: the user's attached Indian-equities breakout prompt, reviewed against
`docs/SPEC.md`, `AGENTS.md`, the installed dependencies and the indexed source.
This is an addition to the existing screener. Each implementation phase below
requires a separate `CONTINUE`; reread the master spec before each phase.

## Intended experience and scope

Add an NSE-only **Breakout** view alongside the existing screener and performance
views. Scan the configured, user-supplied Nifty 500 universe and show up to 50
stocks satisfying all eight breakout rules. Display completed four-hour candles,
their body-based consolidation zone, the evaluated breakout candle, scan duration
and timestamps. Order results by relative volume descending, then ticker ascending
for deterministic ties. Apply the limit after evaluating and sorting all survivors.

The existing RSI/P/E/composite screener remains a separate strategy. Its filters,
rankings, NYSE support, results and historical performance retain their meaning.
Breakout does not require passing that screener first, and does not manufacture
RSI, P/E or composite scores to fit its result model.

Initial universe scope is the existing NSE file, not a hand-written ticker list.
The file is present, but its current constituent completeness has not been audited
in this planning phase. BSE and additional NSE constituents require separately
supplied authoritative files and an approved universe/calendar specification.
Do not represent initial delivery as coverage of all NSE/BSE equities.

## Prompt reconciliation

The prompt describes a standalone app. The following adaptations are explicit
proposals for integration, to be documented in a master-spec extension in B1.
No conflicting application requirement is changed during planning.

| Prompt requirement | Integration proposal |
|---|---|
| Express, Node, `server.js`, port 3001 | Existing Python/FastAPI service, configured host/port |
| `yahoo-finance2` | Extend the existing swappable yfinance provider |
| React/Vite/Tailwind, port 5173 | Existing Streamlit UI and theme |
| Lightweight Charts | Installed Plotly candlesticks, range rectangle and candle annotation |
| Remove USA tab | Breakout is NSE-only; keep the existing NYSE screener |
| `/api/scan`, `/api/chart` | Dedicated `/api/breakout/*` routes using existing async refresh conventions |
| Concurrent `Promise.all` | Bounded provider concurrency, chunk delays, retries and breakers |
| Five-minute cache | Configured 300-second reuse window; retain prior successes after failures |
| Prefer larger companies | Market-cap eligibility floor only; relative-volume ranking remains primary |

No new runtime dependency is expected. Plotly is already pinned in requirements.
New libraries or a frontend replacement would require separate scope approval.

## Existing integration points verified from source

- `app/core/interfaces.py`: `MarketDataProvider` currently downloads daily bars
  and fetches P/E. `ScanResultItem` requires RSI, P/E and composite score.
- `app/providers/yfinance_provider.py`: daily download processing, bounded
  workers, retry logic and separate download/fundamental circuit protections.
- `app/cache/bar_cache.py`: cache currently keys only by ticker and keeps 100
  rows; it cannot safely store hourly and daily histories under the same key.
- `app/services/scanner.py`: `StockScannerService.run_scan` performs the existing
  RSI/volume/P/E pipeline, not the proposed eight breakout checks.
- `app/services/state.py`, `app/scheduler/runner.py`, `app/main.py`: market-keyed
  scanner/state registration, one global scan lock and worker-thread scans.
- `app/api/models.py`, `app/api/routes.py`: existing results/refresh/status
  contracts must remain compatible.
- `ui/api_client.py`, `ui/app.py`: HTTP-only access and existing scan controls.
- `app/cache/history.py`, `app/performance/repository.py`: existing capture
  policy and strategy hashes assume composite-strategy history. A new breakout
  payload must not flow into these paths without explicit support.

Installed versions inspected: yfinance 1.3.0, Streamlit 1.51.0, Plotly 7.1.0,
exchange_calendars 4.13.1. `yf.download` exposes `period`, `interval`, `threads`,
`prepost`, `auto_adjust` and timezone controls. No live download was performed.

## Candle definition: user-confirmed decision

The user selected **complete four-hour bars only**. Use exchange-local,
session-open-anchored windows; never group every four rows across session dates.

For a normal 09:15–15:30 NSE session, use the four hourly bars starting at 09:15,
10:15, 11:15 and 12:15 to construct the 09:15–13:15 candle. Discard the remaining
closing segment; do not carry it into tomorrow. This produces one eligible
four-hour candle per normal session and omits afternoon breakouts by design.
Use calendar-derived session bounds rather than hardcoding these example times.

- Fetch hourly history with the proposed `period=3mo`, `interval=1h`,
  `prepost=False`, and a consistent adjusted-price policy for daily and hourly data.
  Validate actual Yahoo timestamps and adjustment behavior before shipping.
- Every component must represent a completed, contiguous full hour within the
  same session. Time elapsed alone does not prove Yahoo has delivered a final bar.
  Add a configurable publication grace period and re-fetch recent overlapping bars.
- Four-hour OHLCV is first open, maximum high, minimum low, final close and summed
  observed volume. Preserve source timestamps and interval start/end.
- Sort/deduplicate timezone-aware timestamps. Validate finite positive OHLC,
  OHLC consistency and nonnegative volume. Missing/invalid required source bars
  are data failures; do not fill prices or volumes or silently use an older signal.
- A session shortened below four hours produces no eligible candle. Unsupported
  special-session hours remain explicitly unverified under the existing calendar.
- Evaluate the latest expected eligible completed window, plus ten preceding
  eligible candles. Detect missing expected windows separately from planned
  discarded tails and from time before today's first four-hour window completes.
- Preserve the repository's `session_partial` formula: latest signal date is
  exchange-local today AND the market is open. Thus it can be true even for a
  complete four-hour candle. Expose `candle_complete=true` and `volume_projected=false`
  separately; use observed four-hour volume without daily projection.

Normal NSE session hours are published by
[NSE](https://www.nseindia.com/static/market-data/market-timings).
The one-candle consequence above is derived from those hours and the confirmed
complete-only policy. yfinance's
[download API](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)
documents interval controls; successful three-month hourly coverage with the
installed provider remains NOT VERIFIED and is a release gate.

## Eight-rule contract

Let `C`, `O`, `V` refer to the evaluated completed four-hour candle. Let `H` and
`L` be the maximum body top and minimum body bottom across the prior ten candles:
`H=max(max(open, close))`, `L=min(min(open, close))`. Exclude the evaluated candle
from consolidation and relative-volume baselines. All rules must pass.

| # | Rule | Proposed configurable default and boundary |
|---|---|---|
| 1 | Tight consolidation | `100*(H-L)/L <= 12` |
| 2 | Close above body range | `C >= H*1.02`; report `100*(C/H-1)` |
| 3 | Breakout body size | `100*abs(C-O)/O >= 5` |
| 4 | Relative volume | `V / mean(previous 10 four-hour volumes) >= 1.5`, no upper cap |
| 5 | Daily liquidity | Mean volume of 20 completed daily sessions `>= 500000` |
| 6 | Market capitalization | Finite reported market cap `>= 50000000`, proposed units INR |
| 7 | Near major closing highs | `C >= 0.90*high20 OR C >= 0.90*high50` |
| 8 | Trend | `C > SMA20 AND C > SMA50` |

For rules 5, 7 and 8, use completed daily sessions whose close timestamp is at or
before the signal candle end. At 13:15 this excludes today's unfinished daily bar.
At a later scan, do not use today's final daily close to reinterpret a 13:15 signal.
This intentionally resolves an ambiguity in the prompt and needs specification
in B1. Require at least 50 valid completed daily sessions; never compute a shorter
SMA/high window. Daily highs mean maximum daily **closing** price, not intraday High.

Other semantics to freeze in B1:

- Rule 3 preserves the prompt's absolute body formula. It does not add a bullish
  `C > O` test. A red candle can pass all eight rules; an emerald highlight marks
  the evaluated signal without concealing its actual open/close direction.
- Rule 7 treats prices above the historical high as passing. Return signed
  `100*(C/highN-1)` values, labelled clearly; do not silently use absolute distance.
- `50000000` INR is ₹5 crore, a low eligibility floor rather than a large-cap
  preference. Preserve the numeric prompt default provisionally, confirm currency
  from provider metadata and label the units. No automatic FX conversion or
  undocumented higher floor.
- Market cap is current reported metadata, with fetch time and source currency.
  It is not a historical point-in-time market-cap measurement for older signals.
- Missing/nonfinite required fields are Failed; valid below-threshold values are
  Filtered Out. A zero volume baseline is invalid; valid zero current volume
  normally fails the relative-volume rule. No fallback or fabricated value.
- The existing strategy's liquidity floor, P/E and RSI rules do not become extra
  breakout eligibility conditions. Optional circuit/reference context may be
  shown as context only, without affecting the eight rules.

## Configuration and provider design

Add validated configuration, documented in `.env.example`; screening code takes
values from Settings rather than literal thresholds. Proposed keys/defaults:

| Keys | Defaults |
|---|---|
| `BREAKOUT_ENABLED` | `false` until the feature is ready to enable |
| `BREAKOUT_INTRADAY_PERIOD`, `BREAKOUT_INTRADAY_INTERVAL` | `3mo`, `1h` |
| `BREAKOUT_DAILY_PERIOD`, `BREAKOUT_CANDLE_HOURS` | `6mo`, `4` |
| `BREAKOUT_CONSOLIDATION_LOOKBACK`, `BREAKOUT_MAX_RANGE_PCT` | `10`, `12` |
| `BREAKOUT_MIN_CLOSE_ABOVE_RANGE_PCT`, `BREAKOUT_MIN_BODY_PCT` | `2`, `5` |
| `BREAKOUT_VOLUME_LOOKBACK`, `BREAKOUT_MIN_RELATIVE_VOLUME` | `10`, `1.5` |
| `BREAKOUT_DAILY_VOLUME_LOOKBACK`, `BREAKOUT_MIN_AVG_DAILY_VOLUME` | `20`, `500000` |
| `BREAKOUT_MIN_MARKET_CAP_INR`, `BREAKOUT_MAX_HIGH_DISTANCE_PCT` | `50000000`, `10` |
| `BREAKOUT_FAST_DAILY_LOOKBACK`, `BREAKOUT_SLOW_DAILY_LOOKBACK` | `20`, `50` |
| `BREAKOUT_MAX_RESULTS`, `BREAKOUT_CACHE_TTL_SEC` | `50`, `300` |
| `BREAKOUT_BAR_GRACE_SEC`, `BREAKOUT_MARKET_CAP_TTL_HOURS` | proposed `300`, `24` |

Validate finite bounds, positive counts/TTLs, compatible lookbacks and supported
candle/interval combinations. Initially constrain aggregation to 4 x 1h; do not
pretend arbitrary timeframe settings are implemented. Reuse existing retry,
worker, chunk and global cooldown settings. Store the actual strategy settings
and candle policy with each successful scan so later config changes do not
reinterpret stored results.

Extend provider capabilities compatibly, preferably with a supplemental typed
breakout protocol for intraday bars and market-cap metadata. Keep legacy daily
`download_bars(tickers)` callers and test doubles functional. Implement the
capability on YFinanceProvider; synthetic implementations are confined to tests
and explicit offline CLI use, never exposed through the production API/UI.

Cache keys must include provider, ticker, interval and adjustment policy. Keep
hourly retention sufficient for the configured period/lookback; do not use the
current 100-daily-row cap for hourly data. Validate overlapping completed bars
and refetch the full affected history after adjustment inconsistencies.

Use cheap price/volume checks before market-cap fetches. Share fundamental
responses safely with existing P/E access where available, with field-specific
validation and TTLs. Missing market cap must not mark valid P/E as unavailable.
Record actual provider attempts including retries; chunking does not reduce the
per-ticker request count. A first scan may need daily plus hourly data for every
constituent; cache reuse and bounded concurrency are necessary.

## API, scheduling and persistence

Proposed routes, separate from the existing public contracts:

| Endpoint | Behavior |
|---|---|
| `GET /api/breakout/results?market=NSE` | Latest successful snapshot, metadata, results and failures |
| `POST /api/breakout/refresh?market=NSE` | Reserve global scan slot; 202 accepted, 429 with retry-after if busy/cooling down |
| `GET /api/breakout/status?market=NSE` | Enabled/scanning state, freshness, deadline, breaker states |
| `GET /api/breakout/chart?ticker=...&scan_id=...` | Candles and annotations captured for that successful scan |
| `GET /api/breakout/export.csv?market=NSE` | Same breakout metrics/order as the results snapshot |

Use Pydantic models. Unknown/unsupported market is 422; disabled NSE/feature is
404. An unavailable chart snapshot is 404 with a specific message. Validate
tickers against the requested snapshot; chart requests do not trigger arbitrary
Yahoo calls. Empty successful scans are 200 with zero results, not scan errors.

Result fields: ticker/name/market, signal close price, candle start/end,
consolidation start/end/high/low/range percentage, breakout percentage/body
percentage, observed volume/baseline/relative volume, daily average volume,
market cap/currency/fetch time, high20/high50/distances, SMA20/SMA50,
`session_partial`, `candle_complete`, `volume_projected`, and eight-rule evidence.
Metadata includes scan ID, strategy version/configuration, signal/data cutoff,
provider fetch time, completion time, duration, request counts, stale reasons,
next scheduled refresh, full passing count and returned count.

Track each symbol once in terminal funnel buckets (failed, first failed rule, or
passed); optional per-rule diagnostics must not double-count failures. Preserve
all failure records even though results are limited to 50. Document additive
breakout-specific failure codes/stages without changing legacy codes.

Register tasks by `(market, strategy)` in the existing scheduler/state wiring.
Keep one scheduler and one global scan reservation across NSE, NYSE, breakout
and existing performance background work. Run provider/scanner and disk work
off the event loop. Adapt startup, closed-market, post-close retry, shutdown,
cooldown and stale handling per strategy rather than duplicating schedulers.

Reuse snapshots for 300 seconds when appropriate; avoid fresh provider work when
no newer eligible candle exists and daily-as-of/metadata remain valid. Refresh
at the next eligible completion plus grace, and retry missing data under existing
backoff/post-close rules. Startup/manual refresh still works when the market is
closed. Surface cache reuse explicitly; do not present it as a new data capture.
TTL expiry alone does not make a retained successful result stale.

Persist atomic, versioned breakout snapshots separately, e.g.
`data/last_scan_NSE_breakout.json`. Preserve the latest success after a failed
scan and load startup snapshots as stale pending validation. Retain the bounded
chart input alongside the snapshot; results and chart use the same scan ID and
thresholds. Publish both atomically, and report unavailable older scan IDs.

Initial delivery provides breakout snapshots/export, not breakout performance
backtests. Do not insert breakout rows into composite history or performance
cohorts. A later explicit phase can add strategy-aware history, deduplicate by
ticker/candle-end/configuration and define executable entry times. Current
reported market cap cannot support claims of historical point-in-time screening.

## UI delivery

- NSE Breakout view with Scan Now, scanning feedback, duration, last scanned time,
  signal candle time, next eligible candle and delayed-data/freshness notices.
- Table: ticker/name, signal price in INR, breakout %, body %, relative volume,
  signed distance from 20/50-session closing highs. Optional details show all
  eight checks, thresholds, liquidity and market cap.
- Default sort is backend relative-volume order; search/sorting/export use the
  breakout dataset. Distinguish no successful scan, no qualifying stocks, loading,
  retained stale results and unavailable API states.
- Dropdown across all returned stocks, independent of pagination. Key selection
  by ticker/scan ID; disappearing selections show an unavailable message.
- Plotly OHLC candles, rose consolidation rectangle spanning only its ten-candle
  window, emerald annotation/outline on the evaluated candle, volume subplot,
  IST axis and hover timestamps. Preserve ordinary candle direction colors.
- Chart reads only the API snapshot; changing selection does not scan or refetch
  fundamentals. Use the existing dark theme and footer: educational/research use,
  not financial advice. Show the complete-only/afternoon-exclusion policy.

## Implementation phases and acceptance gates

Complete exactly one phase at a time and report files, commands, actual test
output, assumptions, verified/unverified status, compliance and state summary.

| Phase | Deliverables and expected files | Required acceptance |
|---|---|---|
| B1: Freeze specification | Extend `docs/SPEC.md`; update this plan, `.env.example`, `app/core/config.py`; define breakout models/protocols in `app/breakout/` and compatible `app/core/interfaces.py` extensions | Confirm adaptations/as-of/high-distance/currency semantics; config/model validation; existing config and interface tests pass |
| B2: Data and candle construction | `app/breakout/candles.py`, provider capability, interval-aware cache, market-cap cache, SYNTHETIC fixtures and provider/candle tests | Independently calculated OHLCV; no cross-session grouping; all completion/timezone/missing-bar/adjustment cases pass |
| B3: Eight-rule scanner | `app/breakout/indicators.py`, `filters.py`, `scanner.py`, strategy models, scanner tests | Exact inclusive/strict boundaries, all-eight AND behavior, as-of daily windows, deterministic ranking/top-50 and complete funnel accounting |
| B4: Backend integration | Breakout routes/state/persistence; adapt `app/main.py`, scheduler and legacy state wiring; async/API/cache tests | Global single-flight, 202/422/404/429, responsive existing results endpoint, independent snapshots, rollback/restart/stale handling, immutable chart consistency |
| B5: Streamlit view | `ui/breakout.py`, `ui/api_client.py`, `ui/app.py`, targeted UI tests | HTTP-only access, chart/range alignment, selection persistence, correct labels/empty/error/stale states and browser visual verification |
| B6: Release verification | README operational notes, spec compliance audit, targeted regression fixes and recorded verification | Full network-free suite passes; approved bounded real-data probe verifies installed Yahoo hourly/currency semantics; no composite/NYSE/history regression |

Suggested commands during implementation, not executed during planning:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_breakout_config.py tests/test_breakout_candles.py tests/test_breakout_provider.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_breakout_scanner.py tests/test_breakout_api.py tests/test_breakout_ui.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

Create only the tests belonging to the current phase. Tests mock Yahoo and use
fictitious tickers with labelled SYNTHETIC fixtures. No test data is served by
the production UI/API. Live tests remain opt-in, excluded from the default suite.

Critical cases: insufficient 11 four-hour/50 daily candles; exactly 12/2/5/1.5
and liquidity/cap/high/SMA boundaries; wick versus body ranges; absolute red body;
zero baselines; all NaN/missing values; missing latest expected candle; before/after
13:15 and grace; holidays/short sessions; aware/naive timestamps; unsorted duplicates;
daily lookahead prevention at afternoon/post-close scans; stale intraday input;
rate limits and breaker aborts; TTLs with fake Clock; expired fundamentals;
one failed symbol versus systemic failure; chart scan-ID consistency; chart/data
cache interval isolation; empty success; more than 50 passing stocks; startup
restore; simultaneous legacy/breakout refresh; shutdown draining; legacy JSON/
SQLite performance unaffected; no API/UI FakeProvider path.

## Planning verification and next step

VERIFIED: master spec and attached prompt read; indexed integration points
inspected; configured NSE universe file exists; listed installed package versions
and yfinance signatures inspected; user selected complete-only candles; official
normal-session hours checked. Initial Git status was clean.

NOT VERIFIED: live hourly availability/timestamps/finalization, current constituent
completeness, reported market-cap currency/coverage, special-session completeness,
feature functionality or performance. No application tests or market-data scans
were run because this deliverable is documentation only.

First next step: on `CONTINUE`, reread `docs/SPEC.md` and complete **B1 only**,
making the integration extension and proposed semantics explicit before data or
scanning implementation. Resolve any remaining conflicting interpretation before
changing application behavior.

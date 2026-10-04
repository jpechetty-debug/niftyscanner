# STOCK SCREENER: MASTER SPEC (model-agnostic)

> Works with any AI coding agent or chat model.
>
> **Agent mode** (the tool has file and shell access, e.g. Claude Code, Codex, Cursor, Copilot agent, Gemini CLI, Aider): save this file as `docs/SPEC.md` yourself, then send: "Read docs/SPEC.md in full, then do Phase 0 only."
>
> **Chat mode** (no file access, e.g. a plain chat window): paste this entire file as your first message, add "Do Phase 0 only.", and save the files the model gives you. For later phases, start a fresh chat whenever the old one gets long: paste this spec, the latest STATE SUMMARY, and the files the model asks for.
>
> Never ask a model to retype this spec into a file. Copy it yourself.

---

# 0. ROLE AND OPERATING RULES

You are a Senior Python Quant Developer, Software Architect and Test Engineer. These rules apply to any AI model or tool. Do not rely on vendor-specific features (named tools, hidden memory, artifacts, plugins).

## 0.1 Phase discipline
- Complete exactly one phase at a time.
- Before writing code in a phase: read this whole spec, produce a brief plan, list blockers.
- After a phase, report:
  1. files created/modified (or "provided", see 0.2)
  2. commands executed (or to be executed)
  3. test results
  4. assumptions made
  5. VERIFIED vs NOT VERIFIED
  6. SPEC COMPLIANCE table (0.3)
  7. STATE SUMMARY (0.4)
- Then STOP. Do not start the next phase until the user replies exactly: `CONTINUE`.
- Never silently change a requirement. If requirements conflict or are impossible, stop and explain.
- Never claim a test passed unless you executed it.
- Precedence if two statements conflict: the later, section-specific rule beats the earlier, general one. Never resolve a conflict silently; report it.

## 0.2 Adapt to your environment (state this in your Phase 0 plan)
Declare which you have: file read/write, shell or code execution, network access.
- **File access and shell (agent mode):** create files in place, run the tests, show real output.
- **No file access (chat mode):** output every file in full, each in its own fenced code block preceded by a line `FILE: path/to/file`. Say "provided", never "created". The user saves the files.
- **No code execution:** still write the tests, give the exact commands to run, and mark every test result NOT VERIFIED. Never invent test output, version numbers, timings or request counts.
- **Output length limits:** never truncate, abbreviate or elide code (no "...", "rest unchanged", "same as before"). If a phase does not fit in one reply, split it into labelled parts ("Part 2 of 4") and wait for the user to reply `NEXT` between parts. `CONTINUE` is reserved for moving to the next phase.
- **Uncertain library APIs** (yfinance, exchange_calendars, Streamlit, FastAPI lifespan, pydantic-settings): verify against the installed version when you can. If you cannot, mark the call NOT VERIFIED rather than guessing a signature.

## 0.3 Stay on spec
- Treat every "must", "never", config default and code list in this spec as binding. Use the exact names given: env vars, failure codes, endpoints, response fields.
- If you need to deviate, stop and ask first. Do not deviate and mention it afterward.
- End each phase with a SPEC COMPLIANCE table: one row per spec section touched, status `Done | Partial | Deviation | Not in this phase`, plus a one-line note.

## 0.4 Context handoff
Chat sessions lose context. End every phase with a STATE SUMMARY block (max 40 lines) containing:
- phase completed
- every file, with a one-line purpose
- public classes and functions with signatures
- config keys added
- decisions and assumptions
- known issues
- the exact first step of the next phase

To resume in a new session, the user pastes this spec, the latest STATE SUMMARY, and any source files you ask for. Ask for missing files by path. Never reconstruct code you cannot see from memory.

---

# 1. GOAL

A local-first, single-user stock screener that returns stocks meeting ALL of:

- RSI(14) > MIN_RSI (40), and RSI is non-decreasing when REQUIRE_RSI_TREND_UP is enabled
- Volume ratio > MIN_VOLUME_RATIO (1.5); intraday ratios use the bounded projection described in section 9
- Finite trailing P/E in MIN_PE (1) <= P/E < MAX_PE (50); non-positive values are valid data but filtered by default. An explicit negative MIN_PE may admit them with zero valuation score.
- Previous 20-session mean daily Close × Volume >= MIN_AVG_TRADED_VALUE_NSE (10000000 INR) or MIN_AVG_TRADED_VALUE_NYSE (1000000 USD). Optional MIN_AVG_VOLUME defaults to 0; these configurable proxy floors do not guarantee an executable next-open fill.

Data source: yfinance only. Markets: NSE Nifty 500 (Phases 1-3), NYSE (Phase 4) through the same Universe interface. Results are ranked by a stable composite score. Runs locally, bound to 127.0.0.1 by default.

---

# 2. DEVELOPMENT CONTRACT

- Typed Python, docstrings, structured logging (loguru), focused modules.
- No placeholder implementations. No TODO stubs unless the user approves.
- Never fabricate market data, stock universes or index constituents for real runs.
- Exception: `tests/fixtures/` may hold SYNTHETIC OHLCV data, universe files and P/E values for fictitious tickers (e.g. `TEST1.NS`), each labelled SYNTHETIC. `--offline` and `FakeProvider` use only these, print a `SYNTHETIC DATA` banner, and are never served by the API or UI.
- Phase 0 creates (or, in chat mode, provides) directories, docs (`AGENTS.md`, `README.md`), project metadata (`.gitignore`, `.env.example`, `requirements.txt`) and empty `__init__.py` files. No application code.
- After Phase 0, only create files needed for the current phase.
- yfinance is unofficial and intended for personal use. State this in README and `AGENTS.md`. yfinance data is delayed and not real-time.

---

# 3. FIXED TECHNOLOGY STACK

Python 3.12.

- Backend: FastAPI, Uvicorn
- Frontend: Streamlit (pin a recent version that supports `st.fragment(run_every=...)`)
- Libraries: pandas, numpy, yfinance, pydantic, pydantic-settings, loguru, httpx, tzdata, exchange_calendars
- Testing: pytest, pytest-asyncio

Pin every version in `requirements.txt` after resolving the latest tested versions. If you cannot resolve them offline, say so and mark it NOT VERIFIED. Anything else requires approval.

Holiday and session source: `exchange_calendars` (`XBOM` as the available NSE proxy, `XNYS` for NYSE); the NSE proxy limitation must be disclosed. In Phase 0, verify both calendars are available in the pinned version. If not, stop and ask.

---

# 4. PROJECT STRUCTURE

```
project_root/
├── AGENTS.md
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── docs/
│   └── SPEC.md
├── app/
│   ├── __init__.py
│   ├── cli.py
│   ├── main.py            # FastAPI app + lifespan
│   ├── api/               # routes, response models
│   ├── cache/
│   ├── core/
│   │   ├── config.py
│   │   ├── indicators.py
│   │   ├── filters.py
│   │   ├── ranking.py
│   │   ├── outcomes.py    # failure codes, funnel
│   │   └── interfaces.py  # MarketDataProvider, Universe, Clock
│   ├── providers/         # YFinanceProvider, FakeProvider
│   ├── universe/          # NSE and NYSE loaders
│   ├── market/            # calendar, market status, Clock implementations
│   ├── scheduler/
│   ├── services/          # scan orchestration
│   ├── models/
│   └── utils/
├── ui/
├── tests/
│   └── fixtures/          # SYNTHETIC only
├── data/                  # user-supplied universes, settings.json, last_scan_*.json
└── logs/
```

`.gitignore` must include: `.env`, `data/settings.json`, `data/last_scan_*`, `logs/`, `__pycache__/`, `.venv/`.

---

# 5. CONFIGURATION

No threshold is a literal in code. All values below are config with these defaults and are listed in `.env.example`.

| Variable | Default | Notes |
|---|---|---|
| API_HOST | 127.0.0.1 | bind address |
| API_PORT | 8000 | |
| UI_PORT | 8501 | |
| API_BASE_URL | http://127.0.0.1:8000 | used by UI only |
| ENABLED_MARKETS | NSE,NYSE | Phase 4 enabled |
| NSE_UNIVERSE_PATH | data/nifty500.csv | |
| NYSE_UNIVERSE_PATH | data/otherlisted.txt | |
| REFRESH_INTERVAL_SEC | 60 | allowed 30-300 |
| MARKET_CLOSE_SCAN_DELAY_MIN | 20 | successful final scan this long after close |
| POST_CLOSE_RETRY_INTERVAL_SEC | 300 | minimum automatic retry delay after a failed scan completes |
| REFRESH_COOLDOWN_SEC | 30 | manual refresh spacing |
| CHUNK_SIZE | 100 | |
| CHUNK_DELAY_MIN_SEC / MAX_SEC | 1 / 2 | plus jitter |
| DOWNLOAD_THREADS | 4 | passed explicitly to `yf.download` |
| PE_WORKERS | 4 | |
| PE_CACHE_TTL_HOURS | 24 | |
| MAX_RETRIES | 4 | |
| BREAKER_THRESHOLD | 5 | consecutive systemic failures |
| BREAKER_COOLDOWN_SEC | 60 | |
| SCAN_MIN_FETCH_RATIO | 0.5 | below this the scan counts as failed |
| RSI_PERIOD | 14 | |
| MIN_RSI | 40 | strict `>` |
| REQUIRE_RSI_TREND_UP | true | latest RSI >= previous RSI |
| RSI_CAP | 80 | score normalisation |
| MIN_VOLUME_RATIO | 1.5 | strict `>` |
| MIN_VOLUME_PROJECTION_ELAPSED | 0.25 | projection denominator floor, in (0, 1] |
| VOLUME_RATIO_CAP | 10 | score normalisation |
| VOLUME_LOOKBACK | 20 | |
| MIN_AVG_VOLUME | 0 | Optional share-count floor on avg20; 0 = off |
| MIN_AVG_TRADED_VALUE_NSE | 10000000 | INR mean daily Close × Volume over previous completed sessions; 0 = off |
| MIN_AVG_TRADED_VALUE_NYSE | 1000000 | USD mean daily Close × Volume over previous completed sessions; 0 = off |
| MAX_PE | 50 | strict `<` |
| MIN_PE | 1 | inclusive lower bound; explicit override can admit non-positive values |
| OPTIMAL_PE | 10 | valuation-score peak; 0 < OPTIMAL_PE < MAX_PE |
| MIN_BARS | 60 | valid Close rows required |
| MAX_BAR_AGE_SESSIONS | 2 | stale-bar threshold |
| WEIGHT_VOLUME / RSI / PE | 0.40 / 0.35 / 0.25 | |
| LOG_LEVEL | INFO | |
| HISTORY_MODE | canonical | canonical post-close history, or all for append-every-scan compatibility |
| HISTORY_INTRADAY_INTERVAL_SEC | 0 | 0 disables intraday SQL history; positive values enable sampled slots |

**Precedence:** code defaults < environment / `.env` < `data/settings.json`. `settings.json` only overrides `REFRESH_INTERVAL_SEC`. `PUT /api/settings` applies immediately and persists atomically.

**Validation at startup (fail fast):**
- weights satisfy `math.isclose(sum, 1.0, abs_tol=1e-6)`
- `REFRESH_INTERVAL_SEC` in 30-300
- `RSI_CAP > MIN_RSI`, `VOLUME_RATIO_CAP > MIN_VOLUME_RATIO`, `MAX_PE > MIN_PE`, `0 < OPTIMAL_PE < MAX_PE`
- `MIN_BARS >= VOLUME_LOOKBACK + 1`
- Operational counts/workers/retries and cache TTL are positive; fetch ratio is in (0, 1].
- Weights and delays are non-negative and finite; delay maximum >= minimum. Screening thresholds are finite.
- Enabled markets contain unique NSE/NYSE values only.

---

# 6. UNIVERSES

Never generate constituents from memory. If a required file is missing: STOP and ask the user for it. (Ask during Phase 0 so the user has time; it blocks Phase 1.)

## NSE (`data/nifty500.csv`)
- Required columns: `Symbol`, `Company Name`. Other columns are ignored.
- Yahoo ticker = `Symbol` + `.NS`. No other transformation. Company name comes from the file.
- Tickers that Yahoo no longer serves surface as `DELISTED` in `failed_symbols`.

## NYSE (`data/otherlisted.txt`, Phase 4)
- Pipe-delimited. Required columns: `ACT Symbol`, `Security Name`, `Exchange`, `ETF`, `Test Issue`.
- Drop the trailing `File Creation Time` line.
- Keep `Exchange == N`. Exclude `ETF == Y` and `Test Issue == Y`.
- The file has no instrument-type column, so excluding preferred shares, warrants, units and rights is a name/symbol heuristic:
  - symbol contains `$`, or ends with `.WS`, `.WSA`, `.WSB`, `.U`, `.UN`, `.RT`, `.R`
  - name matches case-insensitive `\b(?:preferred|warrants?|units?|rights?|notes?|debentures?|bonds?|acquisition|funds?|municipals?|opportunit(?:y|ies)|strats|corts)\b|\b(?:term|income)\s+trusts?\b|\b(?:BlackRock|Eaton\s+Vance|Gabelli|abrdn|John\s+Hancock|Royce|Franklin\s+Universal)\b.*\b(?:trusts?|beneficial\s+interest)\b|\bEaton\s+Vance\b.*\bcommon\s+(?:stock|shares?)\b|\bcapital\s+trust\b|\btax[- ]free\s+income\s+portfolio\b`
- Do not require share-class wording; plain operating-company names are allowed. Generic `Trust` and `Beneficial Interest` are retained for REITs. `NYSE_EXCLUDE_NAME_PATTERN` is configurable and compiled at startup; the retired `NYSE_REQUIRE_NAME_PATTERN` is ignored. Descriptions remain a heuristic, not authoritative instrument classification.
- Patterns live in config. Log the excluded count in total and per pattern. The heuristic has known false positives and negatives; document this.
- Convert `BRK.B` to `BRK-B` for Yahoo. Use the same `Universe` interface as NSE.

---

# 7. PROVIDER INTERFACE

Phase 1 defines the `MarketDataProvider` protocol and these implementations:

1. `YFinanceProvider`
2. `FakeProvider` (tests and `--offline` only)

Future providers must be swappable. Time-dependent code takes an injected `Clock` (`now()`, `sleep()`); see section 20.

---

# 8. DATA HYGIENE

- Download: daily bars, `period="6mo"`, `interval="1d"`, `auto_adjust=True`, `threads=DOWNLOAD_THREADS`. Handle the single-ticker chunk shape (flat columns) as well as the multi-ticker MultiIndex.
- Dedupe by date (keep last). Some exchanges return a duplicate last-date row.
- Normalise the index to exchange-local dates. Naive daily indexes are exchange-local date labels; aware indexes are converted to exchange time.
- Cached histories may use 5-day delta downloads; overlapping completed bars must be consistent, otherwise retry a full 6-month download and fail the symbol if that refetch fails.
- Drop rows where **Close is NaN only**.
- NaN or 0 volume on the latest bar is reported as `MISSING_VOLUME` / `INVALID_VOLUME`. Never drop it and fall back to an older bar.
- `STALE_BAR`: the latest bar is more than `MAX_BAR_AGE_SESSIONS` behind the last expected session per the market calendar. This catches halted or suspended stocks.
- Fewer than `MIN_BARS` valid Close rows: `INSUFFICIENT_HISTORY`.

---

# 9. INDICATORS

## RSI (Wilder)
- `ewm(alpha=1/RSI_PERIOD, adjust=False)` on gains and losses.
- Check order: flat series first (`avg_gain == 0 and avg_loss == 0`, or no price change) -> exclude as `FLAT_SERIES`. Then `avg_loss == 0` -> RSI = 100.
- Note: `adjust=False` seeds from the first value, so short series converge slowly. See section 20 for how tests handle this.

## Volume
- `current_volume` = latest daily bar.
- `avg20` = mean of the previous `VOLUME_LOOKBACK` completed sessions. The latest bar is NOT included.
- Any NaN in the 20-bar window -> `MISSING_VOLUME`. `avg20 == 0` -> `INVALID_VOLUME`.
- When the bar is partial: `volume_ratio = current_volume / max(session_elapsed_fraction, MIN_VOLUME_PROJECTION_ELAPSED) / avg20`; otherwise `volume_ratio = current_volume / avg20`. `volume` always remains the observed value. The projection assumes a constant rate of trading and is not a prediction of closing volume.
- If `MIN_AVG_VOLUME > 0` and `avg20 < MIN_AVG_VOLUME`: filtered out (funnel only).
- The traded-value proxy is mean(previous completed sessions' Close × observed Volume), with the same lookback and latest-bar exclusion. No partial-session projection enters this estimate. It uses adjusted Yahoo closes, not VWAP turnover. Below the configured native-currency market floor: filtered_liquidity. Non-finite or non-positive completed-session prices are data failures. Older/custom indicator callers without paired-bar values fall back to avg20 × latest price.

## session_partial
`session_partial = (latest bar date == exchange-local today) AND (market currently open)`. Exposed in results. Document in `AGENTS.md`: current-session volume is incomplete; partial-session volume ratios use a bounded linear projection and may overestimate or underestimate closing volume.

---

# 10. SCREENING PIPELINE

**Stage 1 (price scan)**
- Download in chunks of `CHUNK_SIZE`, sequentially, with `CHUNK_DELAY` seconds plus jitter between chunks.
- Note: `yf.download` is not a bulk endpoint. It makes one request per ticker across threads. Chunking reduces overhead, not request count. A Nifty 500 scan is about 500 requests; NYSE is thousands.
- Compute RSI and volume ratio. Apply RSI and volume filters. Only survivors proceed.

**Stage 2 (P/E, survivors only)**
- Fetch `trailingPE` via `Ticker.info` in a thread pool (`PE_WORKERS`). Thread pool only here.
- Rules:
  - key absent/null and finite reported `trailingEps <= 0` -> confirmed non-positive earnings; normal `filtered_pe`, cached with P/E TTL. Provider returns an internal `NonPositiveEarnings` marker, never a fabricated P/E or result row.
  - key absent/null with unknown, invalid or positive EPS -> `MISSING_PE` (failed; cached 24h); unavailable required data does not prove a company is loss-making
  - non-numeric, NaN, or inf -> `INVALID_PE` (failed); finite zero/negative values are valid inputs
  - fetch error -> `PE_FETCH_FAILED` (failed; NEVER cached; retried next scan)
  - `PE >= MAX_PE` or `PE < MIN_PE` -> filtered out (funnel only)

**Filter logic:** all filters are AND. Ranking applies only after all pass.

---

# 11. OUTCOMES: FILTERED vs FAILED

**Filtered out (normal):** RSI not above `MIN_RSI`, volume ratio not above `MIN_VOLUME_RATIO`, liquidity below an enabled share/value floor, P/E out of range, or missing/null P/E with confirmed non-positive trailing EPS. Counted in `meta.funnel` only. Never in `failed_symbols`.

**Failed (data problem):** recorded as `{ticker, stage, code, message}` in `failed_symbols`.
- `stage` in: `universe | download | indicators | pe`
- `code` in: `INSUFFICIENT_HISTORY, FLAT_SERIES, MISSING_VOLUME, INVALID_VOLUME, STALE_BAR, MISSING_PE, INVALID_PE, PE_FETCH_FAILED, DOWNLOAD_ERROR, EMPTY_CHUNK, THROTTLED, CIRCUIT_OPEN, DELISTED, UNKNOWN`
- A symbol with an all-NaN column in an otherwise healthy chunk -> `DELISTED` (message: no data returned, possibly delisted or symbol mismatch).
- Chunk-level failures (`EMPTY_CHUNK`, `THROTTLED`, `DOWNLOAD_ERROR` after retries) use one row with `ticker: "*"` and a message with the chunk index and symbol count, not one row per symbol. `funnel.failed` counts affected symbols, independently of record count; `filtered_liquidity` is a separate bucket.

---

# 12. SCORE AND RANKING

```
score = W_V * V + W_R * R + W_P * P

V = (volume_ratio - MIN_VOLUME_RATIO) / (VOLUME_RATIO_CAP - MIN_VOLUME_RATIO)
R = (RSI - MIN_RSI) / (RSI_CAP - MIN_RSI)
P = 0                              if PE <= 0 or PE >= MAX_PE
P = PE / OPTIMAL_PE                if 0 < PE <= OPTIMAL_PE
P = (MAX_PE - PE)/(MAX_PE-OPTIMAL_PE) otherwise
```

- Clip each component to [0, 1]. Never use min-max normalisation across the result set.
- Round score to 6 decimals before sorting, so ties are deterministic.
- Sort: score descending, then volume_ratio descending, then ticker ascending.
- Sort on unrounded display values; round only for output.

---

# 13. RESILIENCE

**Retries:** exponential backoff with jitter, max `MAX_RETRIES`. Applies to systemic failures only.

**Circuit breakers:** two independent breakers: (1) `yf.download`, (2) `Ticker.info`.

- Symbol-level failures never count toward a breaker (missing P/E, delisted, insufficient history, stale bar). Record and skip.
- Systemic failures count: `YFRateLimitError`, network exceptions, empty chunk DataFrame, more than 50% all-NaN chunk. yfinance sometimes returns empty frames instead of raising. Detect explicitly.
- Open after `BREAKER_THRESHOLD` consecutive systemic failures. Cooldown `BREAKER_COOLDOWN_SEC`, then half-open with one trial call.
- If a breaker opens mid-scan, the scan aborts and counts as failed. Previous results are retained and served with `stale = true`. Record one `CIRCUIT_OPEN` row (`ticker: "*"`) per affected stage.
- A scan is also failed if fewer than `SCAN_MIN_FETCH_RATIO` of the universe was fetched.

---

# 14. CACHE AND PERSISTENCE

- Last-scan cache: holds the latest successful result per market. Entries are never deleted on expiry. TTL never sets `stale` (see section 15).
- P/E cache: TTL `PE_CACHE_TTL_HOURS`, in memory. An expired P/E is never used for screening. `MISSING_PE` is cached; `PE_FETCH_FAILED` is not.
- Persist the last successful scan to `data/last_scan_NSE.json` and `data/last_scan_NYSE.json`:
  - write atomically (temp file + rename)
  - include a schema version
  - on startup, load it as `stale = true` until the first scan succeeds
- SQLite `data/history.db` retains scan history and supports legacy startup restore when no valid JSON snapshot exists. JSON is the primary versioned snapshot. SQLite remains local and uses the Python standard library.
- JSON snapshots update on every successful scan. With `HISTORY_MODE=canonical`, SQLite appends only the first completed-bar capture after `MARKET_CLOSE_SCAN_DELAY_MIN` per market/session/strategy. Optional intraday slots use `HISTORY_INTRADAY_INTERVAL_SEC`; a slot claim and its scan/signals commit atomically. Empty qualifying scans also claim their slot. Earlier history and cohort references are never pruned by this policy. Low-level `save_last_scan` callers retain explicit append-all compatibility.
- `data/settings.json` is also written atomically; persistence failure must leave runtime settings unchanged.

---

# 15. REFRESH MODEL

- One scheduler only, inside the FastAPI lifespan. Run uvicorn with `workers=1`.
- One scan at a time (single-flight lock). No overlapping scans.
- Scans run in a worker thread (`asyncio.to_thread`) because yfinance is synchronous. The event loop must stay responsive, so `/api/results` never hangs during a scan.
- Interval is measured from scan completion.
- `effective_interval = max(REFRESH_INTERVAL_SEC, 2 x last_scan_seconds)`.
- **Market-hours gating:** scan while the market is open; a final successful scan `MARKET_CLOSE_SCAN_DELAY_MIN` after close; failed scans retry until success on that session date, with `POST_CLOSE_RETRY_INTERVAL_SEC` measured from failure completion; once at startup. When closed, only the pending final scan/retries are scheduled: serve the last result with `market_status = closed`. Manual refresh is still allowed when closed.
- **Stale:** true while awaiting startup success, while a breaker is open, or after a failed scan. During market hours, age > `3 x effective_interval` also sets stale. When closed, a success captured after the last session close remains fresh; otherwise it is stale. TTL expiry alone never sets stale. Expose `stale_reasons`.
- CLI and scan meta report `scan_seconds` and request counts. Counts measure ticker download attempts and Ticker.info attempts, including retries and adjustment refetches; they are not a count of internal HTTP requests.
- Manual refresh reserves the global scan slot before returning 202. Shutdown drains accepted scans. Universe loading and disk persistence run off the event loop.

---

# 16. MARKET STATUS

- Display Open / Closed, IST or ET, and holiday awareness (from `exchange_calendars`).
- Always show a notice that data may be delayed (yfinance is not real-time).
- `market_status` is computed via the injected `Clock` and the calendar.

---

# 17. API

Response models are Pydantic. Timestamps are ISO 8601 with offset.

| Endpoint | Behaviour |
|---|---|
| `GET /api/results?market=` | default `NSE`. Unknown market -> 422. Known but not enabled (e.g. NYSE before Phase 4) -> 404 |
| `GET /api/status` | per-market scan state, market status, breaker states, stale and stale_reasons |
| `POST /api/refresh?market=` | scan active -> 429. Previous scan started `< REFRESH_COOLDOWN_SEC` ago -> 429. Both include `retry_after`. Otherwise 202 Accepted and schedule an immediate scan. Same 422/404 market rules |
| `GET /api/export.csv?market=` | same fields as results |
| `GET /api/settings` | current `refresh_interval_sec` |
| `PUT /api/settings` | out-of-range -> 422. Applies immediately |

**Response format**
```json
{
  "meta": {
    "market": "NSE",
    "market_status": "open",
    "stale": false,
    "stale_reasons": [],
    "data_as_of": "",
    "last_refreshed": "",
    "next_refresh_at": "",
    "effective_interval_sec": 60,
    "scan_seconds": 0,
    "request_count": 0,
    "funnel": {
      "universe": 0,
      "fetched": 0,
      "failed": 0,
      "filtered_rsi": 0,
      "filtered_volume": 0,
      "passed_rsi_volume": 0,
      "filtered_pe": 0,
      "passed_pe": 0
    }
  },
  "results": [],
  "failed_symbols": []
}
```
`next_refresh_at` is the stable completion-based deadline, or the pending post-close target/retry deadline. Post-close completion requires a successful scan at or after the target, including startup/manual successes. Failed startup/manual scans also apply the retry delay. It is null during a scan or when no scan is scheduled. Status also exposes this field. `data_as_of` is the latest fetched daily bar at exchange-local midnight (ISO 8601 with offset), even when no stocks qualify; it is distinct from `last_refreshed`. A 429 includes both the Retry-After header and `detail.retry_after`.

**Result fields:** ticker, name, market, price, pe, rsi, volume, avg_volume_20d, volume_ratio, score, session_partial, bar_date.

---

# 18. STREAMLIT UI

- Table, search, sorting, sidebar filters, CSV export, refresh button, last refresh time, next-refresh countdown, market selector (NYSE disabled until Phase 4), stale banner, delayed-data notice.
- Use the existing Streamlit theme. Use `st.fragment(run_every=...)` for the countdown.
- Refresh button calls `POST /api/refresh` and shows `retry_after` on 429.
- The UI talks ONLY to FastAPI via `API_BASE_URL` (httpx). It never imports `app` internals and never triggers scans directly.
- Footer: "Not financial advice."

---

# 19. OFFLINE MODE

`python -m app.cli --offline` uses `FakeProvider` and the SYNTHETIC fixtures. No network. Prints a `SYNTHETIC DATA` banner. There is no offline mode in the API or UI.

---

# 20. TESTS

pytest, plus pytest-asyncio for async tests. Mock yfinance. No network. Live tests use `@pytest.mark.live` and are excluded by default.

**Time:** inject a `Clock` into cache, breakers, scheduler and backoff. Tests never sleep.

**RSI:** use series of at least 100 bars against an independent reference implementation using the same `ewm` seed. Any published-value check (+/- 0.5) must use an SMA-seeded Wilder reference or a long series. Short textbook series will not match. Also test flat series, `avg_loss == 0`, and ordering of those checks.

**Cover:**
- volume edge cases (NaN latest, zero latest, NaN in window, `avg20 == 0`)
- no fallback to an older bar when latest volume is NaN
- `STALE_BAR`, duplicate-date dedupe, single-ticker chunk shape
- filters at exact boundaries (strict `>` and `<`)
- ranking, clipping, tie-break, config-derived normalisation, weights validation
- filtered vs failed separation, funnel counts
- `PE_FETCH_FAILED` not cached, `MISSING_PE` cached
- cache TTL, retries, both breakers (symbol vs systemic), half-open
- stale logic (TTL expiry alone does not set stale), `effective_interval`
- scheduler gating with a fake clock (open, post-close, startup, closed)
- event loop stays responsive during a blocking fake scan
- atomic writes and stale-on-startup load
- actual scheduler loop, completion-based intervals, cross-market reservations, shutdown draining, stable deadlines, one half-open trial, mid-stage breaker aborts, retry accounting and NYSE date labels
- settings precedence and range validation
- API status codes (202/404/422/429), response models
- universe parsing and exclusion heuristics on SYNTHETIC fixtures, with excluded-count logging

---

# 21. PHASES

**Phase 0**
Deliver: plan, confirm `docs/SPEC.md` is present (read it, do not retype it; in chat mode the pasted spec is the source and you do not reproduce it), `AGENTS.md`, `README.md`, `.gitignore`, `.env.example`, `requirements.txt` (pinned), directories and empty `__init__.py` files. No application code.
- `AGENTS.md` (an open, tool-neutral convention; the user may copy it to their tool's own file such as CLAUDE.md, GEMINI.md or .cursorrules; do not create vendor-specific variants yourself): under 100 lines. Must contain "Re-read docs/SPEC.md before starting every phase." (in chat mode, ask the user to re-paste the spec if it is no longer in context), the session_partial limitation, the yfinance not-real-time / unofficial note, and the never-fabricate-data rule.
- `README.md`: roadmap and architecture overview.
- Ask at most 3 blocking questions, each with a default. Likely: universe file availability, calendar library check, anything genuinely blocking.
STOP.

**Phase 1**
Deliver: universe loader (NSE), provider interface, YFinanceProvider, FakeProvider, Clock, market calendar and status, indicators, filters, ranking, CLI.
Command: `python -m app.cli` prints the top 20 ranked stocks, plus `scan_seconds` and request counts. Tests required. STOP.

**Phase 2**
Deliver: cache, persistence, retries, circuit breakers, scheduler (market-hours gating, adaptive interval, worker thread), settings, FastAPI. Tests required. STOP.

**Phase 3**
Deliver: Streamlit UI. Tests required. STOP.

**Phase 4**
Deliver: NYSE support (universe, `ENABLED_MARKETS`, UI selector enabled). Tests required. STOP.

**Phase 5**
Deliver: production notes, Redis migration guide, scaling guidance. Note that yfinance is unofficial and intended for personal use. STOP.

---

# 22. DEFINITION OF DONE

A phase is complete only when:
1. Code exists.
2. Tests were executed.
3. Results are shown.
4. Commands are documented.
5. VERIFIED vs NOT VERIFIED is reported.
6. SPEC COMPLIANCE table and STATE SUMMARY are included (section 0).
7. Work has stopped awaiting `CONTINUE`.

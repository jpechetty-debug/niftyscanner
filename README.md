# Local-First Stock Screener (Nifty 500 & NYSE)

Swing research now has an isolated **S1 data foundation**. The [implementation report](docs/SWING_S1_IMPLEMENTATION.md) records the archive-import checks and current coverage gaps; the [six-phase plan](docs/SWING_TRADING_PLAN.md) defines later setup, risk, replay and validation work. S1 adds no trading signals or scheduled behavior.

```powershell
.venv/Scripts/python.exe -m scripts.swing_data import --source "D:/Codex/Finding Multibagger NSE/multibagger634-main/lite.db" --report "logs/swing-s1/coverage.json"
.venv/Scripts/python.exe -m scripts.swing_data verify --vintage <snapshot_id>
.venv/Scripts/python.exe -m scripts.swing_data report --vintage <snapshot_id>
```

Imports open the source read-only, select the configured NSE universe, freeze bounded price/delivery evidence, and commit to `data/swing.db`. Source accounts/credentials are excluded. Unknown price basis, stale/missing history, unavailable Nifty 500 benchmarks and unsupported calendar sessions remain explicit; imported rows do not become trade-ready merely by passing format checks. Existing scanner endpoints and behavior are unchanged.

A robust, local-first, single-user stock screening pipeline that identifies and ranks equities satisfying technical momentum, volume breakout, and value fundamentals:

- **RSI(14) > 40** (`MIN_RSI`), non-decreasing when `REQUIRE_RSI_TREND_UP=true`
- **Volume ratio > 1.5x** previous 20-session average; partial sessions use bounded linear projection (`MIN_VOLUME_PROJECTION_ELAPSED=0.25`)
- **1 <= trailing P/E < 50** (`MIN_PE`/`MAX_PE`); zero/negative values are filtered by default
- **Average daily traded-value proxy >= ₹10 million for NSE / US$1 million for NYSE** (`MIN_AVG_TRADED_VALUE_NSE` / `MIN_AVG_TRADED_VALUE_NYSE`); configurable, including 0 to disable. The optional share floor (`MIN_AVG_VOLUME`) now defaults to 0.

The valuation score peaks at configurable `OPTIMAL_PE=10`. Observed volume remains unchanged; intraday projection assumes a constant trading rate and can overestimate or underestimate closing volume.

Equities meeting all screening criteria are ranked via a stable composite score combining volume breakout, RSI momentum, and attractive valuation.

> **Data Source Notice:** Market data is sourced via `yfinance`. `yfinance` is an unofficial library intended for personal research use only. Market data provided by `yfinance` is delayed and not real-time.

SQLite history defaults to the first completed post-close capture per market, session and strategy (`HISTORY_MODE=canonical`). JSON snapshots still update on every successful scan. Set `HISTORY_INTRADAY_INTERVAL_SEC=1800` for optional half-hour samples, or `HISTORY_MODE=all` to retain every scan. Existing history is preserved. Run commands from the project root because configured data paths are relative.

NYSE plain company names are allowed. Configurable exclusions target debt, funds, acquisition vehicles, municipal instruments, opportunities vehicles and term/income trusts. Generic `Trust` and `Beneficial Interest` remain allowed for equity REITs. The retired `NYSE_REQUIRE_NAME_PATTERN` setting is ignored. Descriptions remain an imperfect instrument-classification heuristic.

Liquidity uses the mean of paired daily Close × observed Volume over the previous completed `VOLUME_LOOKBACK` sessions, excluding the latest bar. It is a close-based estimate from adjusted Yahoo bars, not exchange VWAP turnover or a guarantee of execution. Each market uses its native currency; there is no FX conversion. Defaults are initial guardrails, not empirically tuned thresholds.

Missing/null P/E with finite reported `trailingEps <= 0` is a normal profitability filter (`filtered_pe`), without inventing P/E. Missing P/E with unknown or positive EPS remains `MISSING_PE`: unavailable required data does not establish that the company is loss-making. Existing numeric P/E filters are unchanged.

### Real-Data Operational Notes & Known Limitations (October 2026 Session Review)
- **NSE Universe Hygiene:** Constituent symbols starting with `DUMMY` (e.g. `DUMMYHEG` placeholder in official Nifty 500 CSV) and Series `RR` (REIT/InvIT units without equity P/E metrics) are automatically filtered out upon load. Series `BE` (trade-for-trade delivery stocks) remain eligible.
- **Default Scan Interval:** Default `REFRESH_INTERVAL_SEC` is 300s (5 min). Saved preferences override the default. Fewer scans reduce load; request totals depend on scan duration, retries and caching, and rate limiting is still possible.
- **NSE Trading Calendar:** The `XBOM` proxy is supplemented with `NSE_EXTRA_HOLIDAYS=2026-01-15`, following [NSE circular 72260](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf). Future calendar completeness and Muhurat hours remain unverified; special sessions remain closed until supported.
- **Intraday Volume Projection:** Early-session projection caps the multiplier at 4x until elapsed time exceeds 25% (~10:49 IST). Morning breakouts may require higher volume to qualify before 11:00 AM.
- **P/E Metric Source:** Filtering/scoring uses Yahoo `trailingPE`. Optional `NSE_REFERENCE_CSV` compares an independently checked, dated P/E and labels matches/divergences; no reference means unverified. Automatic NSE quote access was not available during verification.
- **Circuit Proximity:** Results expose daily move and circuit-warning status. Moves near common 2/5/10/20% bands are explicitly indicative unless a supplied dated reference identifies a fixed band and non-F&O equity. A warning does not establish a locked circuit or executable next-open fill.
- **Tracker Costs:** Performance offers gross and estimated net price returns for 1/5/10 sessions. Net deducts configurable STT, buy stamp duty, brokerage, other charges and slippage; the index remains gross. Stored gross outcomes are preserved. Fixed fees and order-size effects are not modeled.

See [feature configuration, assumptions and verification](docs/FEATURE_COMPLETION.md) for the reference CSV contract and cost calculation.

See [review corrections and verification limits](docs/REVIEW_CORRECTIONS.md) and [real-data review](docs/REAL_DATA_REVIEW.md). The [portable evidence archive](docs/evidence/review-evidence-2026-10-04.zip) includes a consistent history database backup, recorded real probes, constituent files and a network-free verifier.

---

## Architecture Overview

```
                      +---------------------------------------+
                      |             Streamlit UI              |
                      |   (Dark theme, st.fragment refresh)   |
                      +-------------------+-------------------+
                                          | HTTP (httpx)
                                          v
                      +---------------------------------------+
                      |         FastAPI REST Service          |
                      |  - Single-flight scanning scheduler   |
                      |  - Atomic persistence & cache         |
                      |  - Circuit breakers & backoff         |
                      +-------------------+-------------------+
                                          |
                      +-------------------+-------------------+
                      |         Screening Engine              |
                      |  - Stage 1: Batch price/RSI/volume    |
                      |  - Stage 2: Parallel Ticker.info P/E  |
                      |  - Composite Scoring & Ranking        |
                      +-------------------+-------------------+
                                          |
                     +--------------------+--------------------+
                     |                                         |
                     v                                         v
         +-----------------------+                 +-----------------------+
         |   YFinanceProvider    |                 |     FakeProvider      |
         | (Chunked yf.download) |                 |  (SYNTHETIC fixtures  |
         | (Throttling & Backoff)|                 |   for offline & tests)|
         +-----------------------+                 +-----------------------+
```

The NSE calendar uses the available `XBOM` proxy from exchange_calendars; NSE-specific session differences may require a dedicated calendar. Atomic versioned JSON snapshots retain each latest successful scan; SQLite provides historical records and legacy restore. `data_as_of` records the fetched bar date, while `last_refreshed` records scan completion. Request counts measure ticker/provider attempts including retries, not internal HTTP requests.

### Key Architectural Pillars
- **Decoupled Engine & UI:** The Streamlit UI communicates strictly via FastAPI REST endpoints. No internal engine imports or direct scans from the frontend.
- **Fail-Safe & Resilient:** Circuit breakers guard both `yf.download` batch calls and `Ticker.info` fundamental requests. Exponential backoff handles transient network glitches.
- **Transparent Outcomes:** Clear distinction between *Filtered Out* (did not meet screening thresholds) and *Failed* (data issues such as insufficient history, flat series, or missing metrics).
- **Time Inversion:** Clock and calendar abstractions allow testing across market sessions, holidays, and post-close triggers without relying on wall-clock sleep.

---

## Roadmap & Implementation Phases

- [x] **Phase 0: Environment & Project Foundation**
  - Directory structure, empty package modules, pinned `requirements.txt`.
  - Master specification (`docs/SPEC.md`), `AGENTS.md`, `README.md`, `.gitignore`, `.env.example`.
  - Calendar verification and environment audit.
- [x] **Phase 1: Screening Core & CLI**
  - NSE Universe loader (`data/nifty500.csv`), `MarketDataProvider` protocol, `YFinanceProvider`, and `FakeProvider`.
  - Calendar integration (`exchange_calendars`), Market status, Wilder RSI, volume ratio, composite ranking, and CLI (`python -m app.cli`).
- [x] **Phase 2: Backend Service, Caching & Resilience**
  - In-memory P/E caching, atomic JSON persistence, dual circuit breakers, retry backoff.
  - FastAPI server with lifespan background scheduler, market-hours gating, settings override, and status/results endpoints.
- [x] **Phase 3: Interactive Streamlit UI**
  - Real-time auto-refresh countdown (`st.fragment`), interactive tables, sidebar filters, manual refresh with rate-limit cooldown, CSV export.
- [x] **Phase 4: Multi-Market Universe Expansion (NYSE)**
  - NYSE universe loader (`data/otherlisted.txt`), security filtering heuristics, dual-market selector.
- [x] **Phase 5: Production Hardening & Scalability**
  - Production readiness review, Redis migration blueprint, distributed worker architecture guidance (`docs/PRODUCTION.md`).

---

## Technology Stack

- **Runtime:** Python 3.12
- **Backend:** FastAPI, Uvicorn
- **Frontend:** Streamlit (`st.fragment` enabled)
- **Data & Computation:** pandas, numpy, yfinance, exchange_calendars
- **Validation & Settings:** pydantic, pydantic-settings
- **Logging & HTTP:** loguru, httpx
- **Testing:** pytest, pytest-asyncio

---

## Usage & Execution

### 1. Setup Environment
```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

### 2. Run CLI Scanner
```bash
# Live scan for NSE (prints top 20, scan time, and request metrics)
python -m app.cli --market NSE

# Live scan for NYSE
python -m app.cli --market NYSE

# Offline synthetic scan (no network, uses synthetic fixtures)
python -m app.cli --offline
```

### 3. Launch Backend API Server
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```
Interactive OpenAPI documentation will be accessible at: `http://127.0.0.1:8000/docs`.

### 4. Launch Streamlit UI
```bash
streamlit run ui/app.py --server.port 8501
```
Open browser at `http://localhost:8501`.

### 5. Run Test Suite
```bash
python -m pytest -v
```

---

## Production Deployment & Architecture Guides

Swing S2 adds independent daily breakout/pullback research plans, ATR/EMA and
21/63-session relative strength, cost-aware entry/stop/target ranges, and a pure
portfolio sizing preview. See [the frozen contract](docs/SWING_S2_CONTRACT.md)
and [implementation evidence](docs/SWING_S2_IMPLEMENTATION.md).

`SWING_ENABLED=false` by default. With an imported real S1 vintage, setting it to
`true` enables `GET /api/swing/results?vintage=<snapshot SHA256>` and
`GET /api/swing/data-quality?vintage=<snapshot SHA256>`; status is always at
`GET /api/swing/status`. Reads do not download data or schedule scans. The current
archive has no verified Nifty 500 benchmark/price basis, so it returns explicit
data blockers and no plans. Quantities remain null through HTTP; the sizing
library requires explicit marked NAV, reconciled cash and held/pending book.
All results remain research only. S3 now adds a durable research ledger and replay
queue; the Trade Ideas UI remains S5.

For S3, keep `SWING_REPLAY_ENABLED=false` until explicitly configuring a research
account. With both swing flags enabled, `POST /api/swing/paper/accounts` requires
an account ID, explicit INR capital, completed starting session and idempotency
key. `POST /api/swing/replay` accepts an explicit vintage, account, through-session,
key and expected account version; it durably queues work for the existing scheduler.
Inspect accounts, plans, events, jobs and results through `/api/swing/paper/*`
and `/api/swing/plans/{id}`. No raw-price/fill/promotion endpoint is provided.
Disabling replay preserves reads and outstanding liabilities. Unresolved orders
retain their reservations and require reviewed reconciliation; a cancellation
cannot pretend they were unfilled. See the [S3 contract](docs/SWING_S3_CONTRACT.md)
and [S3 implementation report](docs/SWING_S3_IMPLEMENTATION.md) for exact policies.

S4 adds offline preregistration and validation through `python -m scripts.swing_validate`.
See [the S4 protocol](docs/SWING_S4_CONTRACT.md) and [implementation report](docs/SWING_S4_IMPLEMENTATION.md).
It reads S3 event chains in a consistent snapshot, computes purged/embargoed
walk-forward summaries, clustered uncertainty, net NAV/baselines and cost/parameter/
delay/gap/halt stress reports. Counterfactual replay uses isolated in-memory books;
it never changes observed bars, production accounts or plan eligibility.

Start with `schema` to inspect the required study JSON. Supply explicit capital,
hypothesis, strategies, complete S2 configurations (`dict(configuration(cfg))` in
the Python research library), expanding train/validation/test folds, future terminal
dates, and a distinct account ID for every variant/window. Include every parameter
tried in `search_budget`; nearby variants change exactly one declared numeric
strategy/risk parameter by at most 20% by default. Freeze dates/thresholds before
test windows begin. Missing data/accounts yield a blocked research report.

```powershell
python -m scripts.swing_validate register --protocol study.json
python -m scripts.swing_validate development --study my-study --output reports/development.json
# One-time opening only after all terminal dates complete; a crash consumes the claim.
python -m scripts.swing_validate holdout --study my-study --output reports/terminal.json
python -m scripts.swing_validate show --report <report-sha256>
python -m scripts.swing_validate verify --report <report-sha256>
python -m scripts.swing_validate audit --vintage <S1-sha256> --output reports/source-audit.json
```

Output files must be new; authoritative study/reports remain in the append-only
`VALIDATION_DB_PATH`. `VALIDATION_GATES__*` environment values supply defaults when
the protocol omits `gates`; registered protocols ignore subsequent environment
retuning. Code, dependency versions and calendar schedules must also stay fixed.
`VALIDATION_SIGNING_KEY` optionally authenticates manifests with HMAC-SHA256. An
absent key is disclosed and blocks promotion; authentication is not an independent
auditor's signature. Keep the original key to verify signed studies/reports.

The current S1 archive cannot establish historical adjustments, point-in-time
membership/publication or execution context and lacks Nifty 500 OHLC. Its audit
therefore stays **RESEARCH_ONLY**. S4 creates no account/capital or real study by
default, and no historical dataset is claimed untouched. A successful statistical
report would be a separate recommendation, never an automatic plan mutation.
S5 now exposes this through **Workspace → Trade Ideas** in Streamlit. Its tabs
cover ideas, source quality/inspection charts, paper research, read-only validation
reports and operations. No real ideas are fabricated to fill an empty screen.
CSV downloaded from the workspace is bundled with that exact server response.
Unknown-basis charts remain inspection-only and never acquire executable levels.
Allocation previews require an existing, current and reconciled research account.

See [the S5 contract](docs/SWING_S5_CONTRACT.md) and
[verification report](docs/SWING_S5_IMPLEMENTATION.md). Source discovery, chart,
workspace, allocation, report and operations reads all use `/api/swing/*` over
HTTP; the UI does not open SQLite, import backend logic or download provider data.
Operations shows actual worker/feature-flag states, including stopped nightly
outcomes and replay workers. Viewing a report never opens a terminal holdout.

`SWING_OPS_DB_PATH` stores revisioned pause/source controls and rollback receipts.
Pause prevents new research capture/entry jobs from starting; existing queued jobs
and liabilities remain visible. An explicit **management-only replay** cancels
known unfilled simulated orders, captures no new plans and updates existing
holdings through S3 exit/mark rules. Unknown reservations are retained. Rollback
restores prior controls/source selection and forces new work/alerts paused; it
does not rewrite strategy parameters, plans, cash or fills. Global swing/replay
opt-in flags are still required. Control requests use the shared scan lock and
revision/idempotency checks; a busy worker returns 429 with retry guidance.

Lifecycle alerts use a **separate** `SWING_LIFECYCLE_WEBHOOK_URL` (empty by default)
and default to paused. Explicit enable snapshots existing ledger heads, so old
events are not backfilled. Messages omit account names, cash and quantities.
Claim-before-POST and a stable event ID preserve uncertainty after timeout/restart;
reviewed retries retain Idempotency-Key, which the recipient must honor. Remote
exactly-once delivery is not guaranteed. Old events stay bound to their original
destination; changing the URL cannot redirect them. No real webhook was configured
or contacted during S5 verification.

S4 reports retain their original fingerprints. Re-evaluation still requires the
frozen code/runtime; S5 does not silently retune an existing study or reuse its
holdout. Actual prospective S6 observation remains unstarted.

S6 adds **Validation → Forward paper reconciliation**, a read-only monitor and
an explicit daily journal CLI. See [the S6 contract](docs/SWING_S6_CONTRACT.md)
and [implementation/evidence report](docs/SWING_S6_IMPLEMENTATION.md).
`SWING_FORWARD_DB_PATH` defaults to `data/swing_forward.db`;
`SWING_FORWARD_MIN_SESSIONS=30` cannot be set below 30. Reads create no database.

Before the first declared open, register a future session window against an
unused, explicitly funded S3 research account seeded on its preceding completed
session. Supply your own account/campaign identifiers, hypothesis, dates and keys:

```text
python scripts/swing_forward.py register CAMPAIGN_ID --account ACCOUNT_ID --start YYYY-MM-DD --end YYYY-MM-DD --hypothesis "Your frozen hypothesis" --key REGISTER_KEY
python scripts/swing_forward.py capture CAMPAIGN_ID --vintage SOURCE_VINTAGE_HASH --key UNIQUE_SESSION_KEY
python scripts/swing_forward.py report CAMPAIGN_ID
python scripts/swing_forward.py verify CAMPAIGN_ID
```

After each close, explicitly import the observed source vintage and complete S3
paper replay before capturing that session, always before the next open. Capture
empty-signal days too. The frozen `signal_end` leaves the maximum holding/entry
horizon for management-only wind-down; do not generate new plans during that tail.
This CLI has no hidden provider download, daily timer or account mutation. It does
not enable disabled replay workers or repair incomplete observations. No live
campaign was created during implementation because capital/dates and usable source
evidence are absent; synthetic test sessions never count as real validation.

Captures freeze actual references, new paper events/plans and source audits in an
append-only chain. Missing days cannot be backfilled or replaced by later data.
Unknown opening capacity, actions, price basis, benchmark, revisions, stale NAV,
late plans and open final liabilities block operational review. Entry gaps remain
normal unfilled research orders. Reported fills are S3 simulations compared with
observed OHLC; there is no broker fill confirmation. All reports retain
`RESEARCH_ONLY`, `paper_eligible=false`, `promotion_allowed=false`. A complete
30-session journal is an operational check; independent S4 statistical, stress
and untouched-holdout gates are still required before a promotion recommendation.
Older frozen reports retain their original code fingerprints; S6 never rewrites them.

The [22 October source-readiness audit](docs/SWING_SOURCE_READINESS_P0.md)
records current imported coverage, exact gaps and the confirmed special-session
calendar discrepancy. The [proposed source-bundle contract](docs/SWING_SOURCE_BUNDLE_CONTRACT.md)
defines the evidence needed for a future replacement importer. The audit does
not repair data or enable swing execution.

For detailed blueprints on production containerization, Redis state migration, Celery/worker horizontal scaling, and legal considerations, see:
- [`docs/PRODUCTION.md`](docs/PRODUCTION.md)
- [`docs/SPEC.md`](docs/SPEC.md)
- [`AGENTS.md`](AGENTS.md)

*Disclaimer: Not financial advice. Designed for educational and algorithmic research purposes.*

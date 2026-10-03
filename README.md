# Local-First Stock Screener (Nifty 500 & NYSE)

A robust, local-first, single-user stock screening pipeline that identifies and ranks equities satisfying technical momentum, volume breakout, and value fundamentals:

- **RSI(14) > 50** (`MIN_RSI`)
- **Latest Volume > 2x** 20-day average volume (`MIN_VOLUME_RATIO` x 20-session SMA)
- **Trailing P/E < 20** (`MAX_PE`)

Equities meeting all screening criteria are ranked via a stable composite score combining volume breakout, RSI momentum, and attractive valuation.

> **Data Source Notice:** Market data is sourced via `yfinance`. `yfinance` is an unofficial library intended for personal research use only. Market data provided by `yfinance` is delayed and not real-time.

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
pip install -r requirements.txt
cp .env.example .env
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
pytest -v
```

---

## Production Deployment & Architecture Guides

For detailed blueprints on production containerization, Redis state migration, Celery/worker horizontal scaling, and legal considerations, see:
- [`docs/PRODUCTION.md`](docs/PRODUCTION.md)
- [`docs/SPEC.md`](docs/SPEC.md)
- [`AGENTS.md`](AGENTS.md)

*Disclaimer: Not financial advice. Designed for educational and algorithmic research purposes.*

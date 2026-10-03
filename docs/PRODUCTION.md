# Production Deployment, Hardening & Scaling Guide

This guide details the operational considerations, deployment patterns, caching migration paths, and horizontal scaling strategies for the **Local-First Stock Screener**.

---

## 1. Data Source Notice & Legal Limitations

> **CRITICAL NOTICE: `yfinance` is an unofficial, open-source scraper/API client intended for personal research and development only.**
>
> 1. **No Service Level Agreement (SLA):** Yahoo Finance does not provide a public SLA for the endpoints accessed by `yfinance`. Endpoints may change, break, or become rate-limited without notice.
> 2. **Delayed Data:** Quotes and fundamental indicators are delayed (typically 15–20 minutes for both NSE and NYSE depending on exchange reporting tiers). It is **not real-time** and must not be used for low-latency or high-frequency automated execution.
> 3. **Intraday `session_partial` Semantics:** When market sessions are active (`session_partial = true`), the current bar volume represents an incomplete trading day. As a result, calculated volume breakout ratios are inherently conservative until session close.
> 4. **Commercial Use:** Any deployment serving paying external customers must swap `yfinance` with an authorized, licensed commercial data feed (e.g., Interactive Brokers Web API, Polygon.io, Alpha Vantage, Refinitiv, or official exchange direct feeds).

---

## 2. Production Deployment & Hardening

The stock screener is designed with a decoupled architecture:
- **FastAPI Backend (`app/main.py`):** Drives background scanning, market-hours scheduling, circuit breaking, atomic persistence, and REST endpoints.
- **Streamlit UI (`ui/app.py`):** Pure frontend client consuming the FastAPI REST API via HTTP.

### 2.1 Single-Node Architecture & Process Management

In a single-node deployment, run the FastAPI backend and Streamlit UI as distinct system services managed by `systemd` (Linux) or Windows Services.

#### Important Invariant: Uvicorn Single-Worker Mode
The scheduler and in-memory single-flight lock (`asyncio.Lock`) are instantiated inside the FastAPI lifespan context.
**You must run Uvicorn with `--workers 1` when using the default in-memory scheduler.**
Running multiple Uvicorn workers without Redis will spawn duplicate schedulers, triggering concurrent scans that saturate network bandwidth and trip Yahoo Finance rate limits.

#### Systemd Service: FastAPI Backend (`/etc/systemd/system/stock-screener-api.service`)
```ini
[Unit]
Description=Stock Screener FastAPI Backend
After=network.target

[Service]
Type=simple
User=screener
WorkingDirectory=/opt/stock-screener
EnvironmentFile=/opt/stock-screener/.env
ExecStart=/opt/stock-screener/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --log-config log_conf.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### Systemd Service: Streamlit UI (`/etc/systemd/system/stock-screener-ui.service`)
```ini
[Unit]
Description=Stock Screener Streamlit UI
After=stock-screener-api.service

[Service]
Type=simple
User=screener
WorkingDirectory=/opt/stock-screener
EnvironmentFile=/opt/stock-screener/.env
ExecStart=/opt/stock-screener/.venv/bin/streamlit run ui/app.py --server.port 8501 --server.address 127.0.0.1 --server.headless true
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

### 2.2 Docker & Containerization

A containerized deployment encapsulates Python 3.12 dependencies and standardizes the runtime environment.

#### Dockerfile (`Dockerfile`)
```dockerfile
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000 8501
```

#### Docker Compose (`docker-compose.yml`)
```yaml
version: "3.8"

services:
  api:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      - API_HOST=0.0.0.0
      - API_PORT=8000
      - ENABLED_MARKETS=NSE,NYSE
      - NSE_UNIVERSE_PATH=data/nifty500.csv
      - NYSE_UNIVERSE_PATH=data/otherlisted.txt
      - REFRESH_INTERVAL_SEC=60
      - CHUNK_SIZE=100
      - LOG_LEVEL=INFO
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:8000/api/status"]
      interval: 30s
      timeout: 5s
      retries: 3

  ui:
    build: .
    command: streamlit run ui/app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
    ports:
      - "127.0.0.1:8501:8501"
    environment:
      - API_BASE_URL=http://api:8000
      - UI_PORT=8501
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped
```

---

### 2.3 Reverse Proxy & Gateway (Nginx)

When exposing the screener securely (e.g., across an internal VPN or private subnet), place Nginx in front of both services to terminate TLS and forward WebSocket traffic required by Streamlit.

```nginx
# Upstream definitions
upstream screener_api {
    server 127.0.0.1:8000;
    keepalive 32;
}

upstream screener_ui {
    server 127.0.0.1:8501;
    keepalive 32;
}

server {
    listen 80;
    server_name screener.internal;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name screener.internal;

    ssl_certificate /etc/ssl/certs/screener.crt;
    ssl_certificate_key /etc/ssl/private/screener.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-Content-Type-Options "nosniff";
    add_header X-XSS-Protection "1; mode=block";

    # API Endpoints
    location /api/ {
        proxy_pass http://screener_api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Generous timeouts for large universe scans
        proxy_connect_timeout 60s;
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
    }

    # Streamlit UI & WebSockets
    location / {
        proxy_pass http://screener_ui;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support for Streamlit interactive rerun
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
}
```

---

### 2.4 Resource Sizing & Capacity Planning

| Metric | NSE (Nifty 500) | NYSE (~2,300 Equities) | Dual-Market Combined |
|---|---|---|---|
| **Tickers** | 501 | ~2,294 | ~2,800 |
| **Download Chunks (100/chunk)** | 6 chunks | 23 chunks | 29 chunks |
| **Historical Bars (6mo/ticker)** | ~125 trading days | ~125 trading days | ~125 trading days |
| **DataFrame Peak Memory** | ~40 MB | ~180 MB | ~220 MB |
| **P/E Info Requests** | ~10–30 survivors | ~50–150 survivors | ~60–180 survivors |
| **Scan Execution Time** | ~35–45 seconds | ~150–220 seconds | ~180–260 seconds |
| **Minimum Node Spec** | 1 vCPU, 1 GB RAM | 2 vCPU, 2 GB RAM | 2 vCPU, 4 GB RAM |

---

## 3. Redis Migration Blueprint

The screener currently relies on local in-memory dictionaries and atomic JSON files on disk. To support multi-process FastAPI workers, distributed schedulers, and zero-downtime restarts, state should be migrated to Redis.

### 3.1 Architecture Comparison

| Functional Area | Current Local Implementation | Redis Distributed Implementation |
|---|---|---|
| **P/E Fundamental Cache** | In-memory dict with threading lock (`app.cache.pe_cache.PECache`) | Redis Hashes or Keys with TTL (`EXPIRE`) |
| **Last Scan Results** | Local atomic JSON (`data/last_scan_*.json`) | Redis String (JSON) or RedisJSON with Hash index |
| **Runtime Settings** | `data/settings.json` via temp file rename | Redis Hash (`screener:settings`) |
| **Single-Flight Scan Lock** | `asyncio.Lock` inside Lifespan | Distributed Lock (Redlock or `SET lock NX PX`) |
| **Circuit Breakers** | In-memory counter & timestamp | Redis Hash tracking count, state, and last failure timestamp |

---

### 3.2 Redis Key Schema Design

All keys are prefixed with `screener:` to avoid namespace collisions.

```text
screener:
  ├── pe:{ticker}                 -> Hash {pe: float, cached_at: ISO8601, missing: bool} [TTL: 86400s]
  ├── results:{market}            -> JSON string containing complete scan payload (meta, results, failed)
  ├── settings                    -> Hash {refresh_interval_sec: 60, updated_at: ISO8601}
  ├── lock:scan:{market}          -> Mutex string (worker_id) [TTL: 600s with heartbeat]
  └── breaker:
      ├── download:{market}       -> Hash {state: "CLOSED", failures: 0, last_failure: 0}
      └── pe:{market}             -> Hash {state: "CLOSED", failures: 0, last_failure: 0}
```

---

### 3.3 Concrete Implementation Examples

#### 1. Redis P/E Cache Adapter (`app/cache/redis_pe_cache.py`)

```python
import json
from typing import Optional, Tuple
import redis

class RedisPECache:
    """Redis-backed P/E cache conforming to PECache protocol."""

    def __init__(self, redis_client: redis.Redis, ttl_hours: int = 24):
        self.client = redis_client
        self.ttl_seconds = ttl_hours * 3600

    def _key(self, ticker: str) -> str:
        return f"screener:pe:{ticker}"

    def get(self, ticker: str) -> Tuple[bool, Optional[float]]:
        data = self.client.get(self._key(ticker))
        if data is None:
            return False, None
        payload = json.loads(data)
        return True, payload.get("pe")

    def set(self, ticker: str, pe_value: Optional[float]) -> None:
        payload = {"pe": pe_value}
        self.client.set(self._key(ticker), json.dumps(payload), ex=self.ttl_seconds)

    def size(self) -> int:
        keys = self.client.keys("screener:pe:*")
        return len(keys)
```

#### 2. Redis Distributed Single-Flight Lock (`app/scheduler/distributed_lock.py`)

```python
import uuid
import asyncio
import redis.asyncio as aioredis

class RedisDistributedLock:
    """Distributed mutex guaranteeing single-flight scans across multiple API/worker nodes."""

    def __init__(self, client: aioredis.Redis, market: str, ttl_sec: int = 600):
        self.client = client
        self.key = f"screener:lock:scan:{market}"
        self.ttl_sec = ttl_sec
        self.token = str(uuid.uuid4())

    async def acquire(self) -> bool:
        # SET key token NX EX ttl
        acquired = await self.client.set(self.key, self.token, nx=True, ex=self.ttl_sec)
        return bool(acquired)

    async def release(self) -> None:
        # Release only if token matches (Lua atomic check-and-del)
        lua_release = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        await self.client.eval(lua_release, 1, self.key, self.token)
```

#### 3. Redis Persistence Adapter (`app/cache/redis_persistence.py`)

```python
import json
from typing import Optional, Dict, Any
import redis

class RedisPersistence:
    """Stores scan results and user settings into Redis."""

    def __init__(self, client: redis.Redis):
        self.client = client

    def save_scan_results(self, market: str, payload: Dict[str, Any]) -> None:
        key = f"screener:results:{market}"
        self.client.set(key, json.dumps(payload))

    def load_scan_results(self, market: str) -> Optional[Dict[str, Any]]:
        key = f"screener:results:{market}"
        raw = self.client.get(key)
        if not raw:
            return None
        return json.loads(raw)

    def save_settings(self, settings_dict: Dict[str, Any]) -> None:
        self.client.set("screener:settings", json.dumps(settings_dict))

    def load_settings(self) -> Dict[str, Any]:
        raw = self.client.get("screener:settings")
        if not raw:
            return {}
        return json.loads(raw)
```

---

## 4. Horizontal Scaling Guidance

When moving beyond a single host or handling additional national markets (e.g., LSE, TSE, ASX), decoupling scanning computation from HTTP request serving is essential.

### 4.1 Task Queue Architecture (Celery / RQ / ARQ)

```
                            +--------------------------+
                            |     Scheduler Daemon     |
                            |   (Celery Beat / Cron)   |
                            +------------+-------------+
                                         | Publishes Scan Job
                                         v
                            +--------------------------+
                            |       Redis Broker       |
                            |   (Task Queues & State)  |
                            +------------+-------------+
                                         |
                +------------------------+------------------------+
                |                                                 |
                v                                                 v
  +---------------------------+                     +---------------------------+
  |    Worker 1 (Stage 1)     |                     |    Worker 2 (Stage 1)     |
  |  - Chunk price downloads  |                     |  - Chunk price downloads  |
  |  - Wilder RSI calculation |                     |  - Wilder RSI calculation |
  |  - Volume breakout check  |                     |  - Volume breakout check  |
  +-------------+-------------+                     +-------------+-------------+
                | Survivors                                       | Survivors
                +------------------------+------------------------+
                                         |
                                         v
                            +---------------------------+
                            |     Worker Pool (Stage 2) |
                            |  - Parallel P/E fetches   |
                            |  - Normalisation & Score  |
                            |  - Save to Redis & Notify |
                            +-------------+-------------+
                                          |
                                          v
                            +---------------------------+
                            |   FastAPI API Replicas    |
                            | (Read results from Redis) |
                            +---------------------------+
```

### 4.2 Global Rate Limiting across Distributed Workers

When multiple worker nodes execute concurrently, uncoordinated requests to Yahoo Finance will immediately trigger IP throttling (HTTP 429) and IP bans.

**Implementation Strategies:**
1. **Centralized Token Bucket in Redis:**
   - Use Redis `EVAL` with a token bucket script so all workers draw download tokens from a single shared pool (e.g., maximum 5 requests/sec globally).
2. **Egress Gateway / Dedicated Proxy IP:**
   - Route all outbound market provider requests through a dedicated HTTP forward proxy (e.g., Squid or Envoy) with built-in connection throttling.
   - *Note on Residential / Rotating Proxies:* Yahoo Finance heavily fingerprints connections (TLS handshake ciphers, cookie cookies, crumb tokens). Generic rotating proxy pools often trigger bot challenges and empty responses. A single clean static IP with strict rate limiting is significantly more reliable than noisy rotating IPs.

---

### 4.3 Swapping Upstream Market Data Providers

The codebase defines a clean interface via the `MarketDataProvider` protocol in [`app/core/interfaces.py`](file:///d:/Codex/scanner-nifty500/app/core/interfaces.py):

```python
@runtime_checkable
class MarketDataProvider(Protocol):
    def download_daily_bars(
        self,
        tickers: List[str],
        period: str = "6mo",
        interval: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        ...

    def fetch_pe_ratios(
        self,
        tickers: List[str],
    ) -> Dict[str, Optional[float]]:
        ...
```

To replace `yfinance` with a commercial provider (such as Interactive Brokers, Polygon.io, or Alpha Vantage), simply implement a class satisfying this protocol:

#### Example: `PolygonProvider`
```python
from typing import List, Dict, Optional
import pandas as pd
from app.core.interfaces import MarketDataProvider

class PolygonProvider:
    """Commercial MarketDataProvider implementation using Polygon.io API."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def download_daily_bars(
        self,
        tickers: List[str],
        period: str = "6mo",
        interval: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        # Implementation using Polygon Aggregates API
        ...

    def fetch_pe_ratios(
        self,
        tickers: List[str],
    ) -> Dict[str, Optional[float]]:
        # Implementation using Polygon Financials API
        ...
```

Injecting the new provider into `ScannerService` requires zero changes to the indicator calculation, volume filtering, scoring, ranking, or web presentation tiers.

---

## 5. Security & Operational Checklist

- [ ] **Bind Address:** Ensure `API_HOST` remains `127.0.0.1` unless behind a firewall, VPN, or reverse proxy.
- [ ] **Non-root Container User:** In `Dockerfile`, add `USER nonroot` before executing services.
- [ ] **Log Retention & Rotation:** Configure Loguru to rotate logs daily and compress older files:
  `logger.add("logs/screener.log", rotation="50 MB", retention="10 days", compression="zip")`.
- [ ] **Monitor Breaker Metrics:** Expose circuit breaker state transitions to alerting platforms (Slack/PagerDuty) via `/api/status`.
- [ ] **Universe File Integrity:** Periodically verify and update `data/nifty500.csv` and `data/otherlisted.txt` against official exchange circulars to capture new listings, mergers, and ticker renames.

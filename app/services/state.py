"""Application scan state manager with multi-market support, persistence, and stale evaluations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger

from app.cache.persistence import (
    load_last_scan_on_startup,
    save_last_scan,
)
from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.core.config import Settings
from app.core.interfaces import Clock, ScanResultItem
from app.core.outcomes import FailedSymbolItem, FunnelCounts
from app.market.calendar import MarketCalendar
from app.market.clock import SystemClock


class ScanStateManager:
    """Manages in-memory scan results, persistence, stale evaluations, and locks per market."""

    def __init__(
        self,
        config: Settings,
        download_breakers: Dict[str, CircuitBreaker],
        pe_breakers: Dict[str, CircuitBreaker],
        calendar: Optional[MarketCalendar] = None,
        calendars: Optional[Dict[str, MarketCalendar]] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self.config = config
        self.download_breakers = download_breakers
        self.pe_breakers = pe_breakers
        self.clock = clock or SystemClock()

        # Multi-market calendars support
        if calendars:
            self.calendars = {k.upper(): v for k, v in calendars.items()}
        elif calendar:
            self.calendars = {calendar.market.upper(): calendar}
        else:
            self.calendars = {
                m: MarketCalendar(m) for m in self.config.enabled_markets_list
            }

        # Markets tracked
        all_markets = set(self.config.enabled_markets_list) | set(self.calendars.keys())
        self.markets = sorted(list(all_markets))

        # One shared single-flight lock across enabled markets
        self._scan_lock = asyncio.Lock()
        self.scan_locks: Dict[str, asyncio.Lock] = {m: self._scan_lock for m in self.markets}
        self.is_scanning: Dict[str, bool] = {m: False for m in self.markets}

        # Last scan timing per market
        self.last_scan_started_at: Dict[str, Optional[datetime]] = {m: None for m in self.markets}
        self.last_successful_scan_at: Dict[str, Optional[datetime]] = {m: None for m in self.markets}
        self.last_scan_completed_at: Dict[str, Optional[datetime]] = {m: None for m in self.markets}
        self.data_as_of: Dict[str, str] = {m: "" for m in self.markets}
        self.last_scan_seconds: Dict[str, float] = {m: 0.0 for m in self.markets}
        self.last_scan_failed: Dict[str, bool] = {m: False for m in self.markets}
        self.last_scan_request_count: Dict[str, int] = {m: 0 for m in self.markets}

        # Memory store per market
        self.results_store: Dict[str, List[ScanResultItem]] = {m: [] for m in self.markets}
        self.failures_store: Dict[str, List[FailedSymbolItem]] = {m: [] for m in self.markets}
        self.funnel_store: Dict[str, FunnelCounts] = {m: FunnelCounts() for m in self.markets}

        # Startup disk loaded state tracking per market
        self._loaded_from_disk_stale: Dict[str, bool] = {m: False for m in self.markets}
        self._load_from_disk_on_startup()

    def _load_from_disk_on_startup(self) -> None:
        """Attempt to restore previous scan from disk on startup for each market."""
        for market in self.markets:
            disk_data = load_last_scan_on_startup(market)
            if disk_data:
                try:
                    self._loaded_from_disk_stale[market] = True

                    raw_results = disk_data.get("results", [])
                    self.results_store[market] = [ScanResultItem(**r) for r in raw_results]

                    raw_fails = disk_data.get("failed_symbols", [])
                    self.failures_store[market] = [FailedSymbolItem(**f) for f in raw_fails]

                    meta = disk_data.get("meta", {})
                    raw_funnel = meta.get("funnel", {})
                    self.funnel_store[market] = FunnelCounts(**raw_funnel)

                    self.data_as_of[market] = meta.get("data_as_of", "")
                    self.last_scan_seconds[market] = meta.get("scan_seconds", 0.0)
                    self.last_scan_request_count[market] = meta.get("request_count", 0)

                    last_ref_str = meta.get("last_refreshed")
                    if last_ref_str:
                        self.last_successful_scan_at[market] = datetime.fromisoformat(last_ref_str)

                    logger.info(
                        f"Restored {len(self.results_store[market])} items from disk for {market}."
                    )
                except Exception as e:
                    logger.warning(f"Could not parse restored disk state for {market}: {e}")

    @property
    def scan_lock(self) -> asyncio.Lock:
        """Global single-flight lock shared by every market."""
        return self._scan_lock

    @property
    def effective_interval_sec(self) -> int:
        """Effective interval for the primary market."""
        return self.get_effective_interval_sec(self.markets[0])

    def get_effective_interval_sec(self, market: str = "NSE") -> int:
        """Formula: effective_interval = max(REFRESH_INTERVAL_SEC, 2 x last_scan_seconds)."""
        m = market.upper()
        if isinstance(self.last_scan_seconds, (int, float)):
            sec = float(self.last_scan_seconds)
        else:
            sec = self.last_scan_seconds.get(m, 0.0)
        return int(max(self.config.REFRESH_INTERVAL_SEC, 2 * sec))

    def evaluate_stale(self, market: str = "NSE") -> Tuple[bool, List[str]]:
        """Evaluate stale state per Section 15 rules for a market."""
        m = market.upper()
        reasons: List[str] = []

        dl_breaker = self.download_breakers.get(m)
        pe_breaker = self.pe_breakers.get(m)
        if dl_breaker and dl_breaker.state == CircuitState.OPEN:
            reasons.append("Download circuit breaker is OPEN")
        if pe_breaker and pe_breaker.state == CircuitState.OPEN:
            reasons.append("P/E circuit breaker is OPEN")

        if isinstance(self.last_scan_failed, bool):
            is_failed = self.last_scan_failed
        else:
            is_failed = self.last_scan_failed.get(m, False)
        if is_failed:
            reasons.append(f"The last scheduled scan for {m} encountered a systemic failure")

        if isinstance(self._loaded_from_disk_stale, bool):
            is_loaded_stale = self._loaded_from_disk_stale
        else:
            is_loaded_stale = self._loaded_from_disk_stale.get(m, False)

        if isinstance(self.last_successful_scan_at, datetime):
            last_ts = self.last_successful_scan_at
        elif isinstance(self.last_successful_scan_at, dict):
            last_ts = self.last_successful_scan_at.get(m)
        else:
            last_ts = None

        if is_loaded_stale:
            reasons.append(f"Loaded {m} from disk on startup; awaiting initial scan")
        elif last_ts is None:
            reasons.append(f"No successful scan has completed for {m} since server startup")
        else:
            now = self.clock.now()
            cal = self.calendars.get(m)
            if cal is not None and not cal.is_market_open(now):
                # closed: fresh if captured after the last session closed
                if last_ts < cal.last_session_close(now):
                    reasons.append(f"No scan has run since the last {m} session closed")
            else:
                eff = self.get_effective_interval_sec(m)
                max_age_sec = 3 * eff
                age = (now - last_ts).total_seconds()
                if age > max_age_sec:
                    reasons.append(
                        f"Last successful scan is {int(age)}s old (exceeds 3x effective interval: {max_age_sec}s)"
                    )

        return len(reasons) > 0, reasons

    def update_scan_success(
        self,
        market: str,
        results: List[ScanResultItem],
        funnel: FunnelCounts,
        failures: List[FailedSymbolItem],
        scan_seconds: float,
        request_count: int,
        data_as_of: str = "",
        persist: bool = True,
    ) -> None:
        """Store successful scan results and persist atomically to disk."""
        m = market.upper()
        now = self.clock.now()
        self.last_successful_scan_at[m] = now
        self.last_scan_completed_at[m] = now
        self.data_as_of[m] = data_as_of
        self.last_scan_seconds[m] = scan_seconds
        self.last_scan_request_count[m] = request_count
        self.last_scan_failed[m] = False
        self._loaded_from_disk_stale[m] = False

        self.results_store[m] = results
        self.funnel_store[m] = funnel
        self.failures_store[m] = failures

        if persist:
            self.persist_last_scan(m)

    def persist_last_scan(self, market: str) -> None:
        """Persist a snapshot; scheduler calls this in a worker thread."""
        m = market.upper()
        payload = self.get_results_payload(m)
        from app.performance.repository import strategy_context
        payload["strategy_context"] = strategy_context(self.config)
        try:
            save_last_scan(market=m, payload=payload)
        except Exception as e:
            logger.error(f"Failed to persist scan atomically for {m}: {e}")

    def update_scan_failure(
        self,
        market: str,
        failures: List[FailedSymbolItem],
        scan_seconds: float,
        request_count: int,
    ) -> None:
        """Record a failed scan while retaining previous results marked stale."""
        m = market.upper()
        self.last_scan_seconds[m] = scan_seconds
        self.last_scan_request_count[m] = request_count
        self.last_scan_failed[m] = True
        self.last_scan_completed_at[m] = self.clock.now()

        self.failures_store[m] = failures   # replace, don't append (unbounded growth)
        logger.warning(f"Scan for {m} failed. Retaining prior results with stale=True.")

    def get_next_refresh_at(self, market: str) -> Optional[datetime]:
        """Stable completion-based deadline, including failed post-close retries."""
        m = market.upper()
        if self.is_scanning.get(m, False):
            return None
        now = self.clock.now()
        cal = self.calendars[m]
        local_date = cal.to_exchange_local(now).strftime("%Y-%m-%d")
        if not cal.calendar.is_session(local_date):
            return None
        close = cal.calendar.session_close(cal.calendar.date_to_session(local_date)).to_pydatetime()
        target = close + timedelta(minutes=self.config.MARKET_CLOSE_SCAN_DELAY_MIN)
        completed = self.last_scan_completed_at.get(m)
        if cal.is_market_open(now):
            if completed is None:
                return None
            deadline = completed + timedelta(seconds=self.get_effective_interval_sec(m))
            return deadline if deadline <= close else target
        successful = self.last_successful_scan_at.get(m)
        if successful is not None and successful >= target:
            return None
        if self.last_scan_failed.get(m, False) and completed is not None:
            return max(target, completed + timedelta(seconds=self.config.POST_CLOSE_RETRY_INTERVAL_SEC))
        return target

    def get_results_payload(self, market: str) -> Dict[str, Any]:
        """Construct full JSON response format required by Section 17 for a market."""
        m = market.upper()
        now = self.clock.now()
        is_stale, stale_reasons = self.evaluate_stale(m)

        cal = self.calendars[m]
        is_open = cal.is_market_open(now)
        market_status_str = "open" if is_open else "closed"

        last_ref = self.last_successful_scan_at.get(m)
        last_ref_str = last_ref.isoformat() if last_ref else ""

        eff_int = self.get_effective_interval_sec(m)
        next_dt = self.get_next_refresh_at(m)
        next_refresh_str = next_dt.isoformat() if next_dt else None

        funnel = self.funnel_store.get(m, FunnelCounts())
        results = self.results_store.get(m, [])
        failures = self.failures_store.get(m, [])

        meta = {
            "market": m,
            "market_status": market_status_str,
            "stale": is_stale,
            "stale_reasons": stale_reasons,
            "data_as_of": self.data_as_of.get(m, ""),
            "last_refreshed": last_ref_str,
            "next_refresh_at": next_refresh_str,
            "effective_interval_sec": eff_int,
            "scan_seconds": self.last_scan_seconds.get(m, 0.0),
            "request_count": self.last_scan_request_count.get(m, 0),
            "funnel": funnel.model_dump(),
        }

        return {
            "meta": meta,
            "results": [r.model_dump() for r in results],
            "failed_symbols": [f.model_dump() for f in failures],
        }

    def get_status_payload(self, market: str = "NSE") -> Dict[str, Any]:
        """Construct payload for GET /api/status for a market."""
        m = market.upper()
        now = self.clock.now()
        is_stale, stale_reasons = self.evaluate_stale(m)
        cal = self.calendars[m]

        last_ref = self.last_successful_scan_at.get(m)
        return {
            "market": m,
            "is_scanning": self.is_scanning.get(m, False),
            "market_status": cal.get_market_status(now).model_dump(),
            "download_breaker": self.download_breakers[m].state.value if m in self.download_breakers else "CLOSED",
            "pe_breaker": self.pe_breakers[m].state.value if m in self.pe_breakers else "CLOSED",
            "stale": is_stale,
            "stale_reasons": stale_reasons,
            "effective_interval_sec": self.get_effective_interval_sec(m),
            "last_scan_seconds": self.last_scan_seconds.get(m, 0.0),
            "last_refreshed": last_ref.isoformat() if last_ref else None,
            "next_refresh_at": (deadline.isoformat() if (deadline := self.get_next_refresh_at(m)) else None),
        }

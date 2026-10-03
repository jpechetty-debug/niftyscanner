"""Background scheduler supporting independent multi-market scanning and market-hours gating."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Union
from loguru import logger

from app.core.config import Settings
from app.core.interfaces import Clock, Universe
from app.market.calendar import MarketCalendar
from app.market.clock import SystemClock
from app.services.scanner import StockScannerService
from app.services.state import ScanStateManager


class Scheduler:
    """Manages scheduled screening runs for one or more enabled markets."""

    def __init__(
        self,
        config: Settings,
        state_manager: ScanStateManager,
        scanner_service: Optional[StockScannerService] = None,
        scanner_services: Optional[Union[StockScannerService, Dict[str, StockScannerService]]] = None,
        universe_loader: Optional[Universe] = None,
        universes: Optional[Union[Universe, Dict[str, Universe]]] = None,
        calendar: Optional[MarketCalendar] = None,
        calendars: Optional[Union[MarketCalendar, Dict[str, MarketCalendar]]] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self.config = config
        self.state = state_manager
        self.clock = clock or SystemClock()

        # Handle scanners
        scanners_arg = scanner_services or scanner_service
        if isinstance(scanners_arg, dict):
            self.scanners = {k.upper(): v for k, v in scanners_arg.items()}
        elif scanners_arg is not None:
            self.scanners = {self.config.enabled_markets_list[0]: scanners_arg}
        else:
            self.scanners = {}

        # Handle universes
        universes_arg = universes or universe_loader
        if isinstance(universes_arg, dict):
            self.universes = {k.upper(): v for k, v in universes_arg.items()}
        elif universes_arg is not None:
            self.universes = {self.config.enabled_markets_list[0]: universes_arg}
        else:
            self.universes = {}

        # Handle calendars
        calendars_arg = calendars or calendar
        if isinstance(calendars_arg, dict):
            self.calendars = {k.upper(): v for k, v in calendars_arg.items()}
        elif calendars_arg is not None:
            self.calendars = {self.config.enabled_markets_list[0]: calendars_arg}
        else:
            self.calendars = {}

        self._stop_event = asyncio.Event()
        self._trigger_event = asyncio.Event()
        self._post_close_done: Dict[str, Optional[str]] = {m: None for m in self.config.enabled_markets_list}
        self._task: Optional[asyncio.Task] = None
        self._bg_tasks: set = set()

    async def start(self) -> None:
        """Start the background scheduler task."""
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="scanner_scheduler_loop")
        logger.info(f"Background screening scheduler started for markets: {self.config.enabled_markets_list}")

    async def stop(self) -> None:
        """Signal the scheduler to stop and await termination."""
        logger.info("Stopping screening scheduler...")
        self._stop_event.set()
        self._trigger_event.set()
        if self._task and not self._task.done():
            await self._task
        logger.info("Screening scheduler stopped.")

    async def execute_scan(self, market: str = "NSE") -> bool:
        """Execute a single scan for a specific market in a worker thread."""
        m = market.upper()
        scanner = self.scanners.get(m)
        universe = self.universes.get(m)

        if not scanner or not universe:
            logger.error(f"No scanner or universe registered for market {m}")
            return False

        lock = self.state.scan_locks.get(m)
        if not lock:
            lock = asyncio.Lock()
            self.state.scan_locks[m] = lock

        if lock.locked():
            logger.warning(f"Scan for {m} already in progress. Skipping trigger.")
            return False

        async with lock:
            self.state.is_scanning[m] = True
            now = self.clock.now()
            self.state.last_scan_started_at[m] = now

            try:
                constituents = universe.load()
                logger.info(
                    f"[{m}] Loaded {len(constituents)} constituents. Running scan in worker thread."
                )

                (
                    results,
                    funnel,
                    failures,
                    req_count,
                    scan_sec,
                    is_success,
                ) = await asyncio.to_thread(scanner.run_scan, constituents)

                if is_success:
                    self.state.update_scan_success(
                        market=m,
                        results=results,
                        funnel=funnel,
                        failures=failures,
                        scan_seconds=scan_sec,
                        request_count=req_count,
                    )
                else:
                    self.state.update_scan_failure(
                        market=m,
                        failures=failures,
                        scan_seconds=scan_sec,
                        request_count=req_count,
                    )
                return is_success

            except Exception as e:
                logger.exception(f"[{m}] Unhandled exception during scan execution: {e}")
                self.state.update_scan_failure(
                    market=m,
                    failures=[],
                    scan_seconds=0.0,
                    request_count=0,
                )
                return False
            finally:
                self.state.is_scanning[m] = False

    def can_trigger_manual_refresh(self, market: str = "NSE") -> Tuple[bool, Optional[int]]:
        """Check if manual refresh for a market is permitted under lock and cooldown."""
        m = market.upper()
        lock = self.state.scan_locks.get(m)
        if self.state.is_scanning.get(m, False) or (lock and lock.locked()):
            return False, self.config.REFRESH_COOLDOWN_SEC

        last_started = self.state.last_scan_started_at.get(m)
        if last_started is not None:
            elapsed = (self.clock.now() - last_started).total_seconds()
            if elapsed < self.config.REFRESH_COOLDOWN_SEC:
                retry_after = int(self.config.REFRESH_COOLDOWN_SEC - elapsed)
                return False, max(1, retry_after)

        return True, None

    def trigger_immediate_scan(self, market: str = "NSE") -> Tuple[bool, Optional[int]]:
        """Trigger an immediate scan for a market asynchronously if permitted."""
        m = market.upper()
        allowed, retry_after = self.can_trigger_manual_refresh(m)
        if not allowed:
            return False, retry_after

        task = asyncio.create_task(self.execute_scan(m))
        self._bg_tasks.add(task)               # keep a ref so it isn't GC'd
        task.add_done_callback(self._bg_tasks.discard)
        return True, None

    async def _run_loop(self) -> None:
        """Main scheduler loop enforcing startup scans, market-hours gating, and post-close runs."""
        # 1. Mandatory scan at startup for all enabled markets
        logger.info(f"Executing startup scans for {self.config.enabled_markets_list}...")
        for market in self.config.enabled_markets_list:
            if self.universes.get(market):
                ok = await self.execute_scan(market)
                cal = self.calendars.get(market)
                if ok and cal and not cal.is_market_open(self.clock.now()):
                    now = self.clock.now()
                    local_date_str = cal.to_exchange_local(now).strftime("%Y-%m-%d")
                    if cal.calendar.is_session(local_date_str):
                        close_ts = cal.calendar.session_close(cal.calendar.date_to_session(local_date_str))
                        if now >= close_ts + timedelta(minutes=self.config.MARKET_CLOSE_SCAN_DELAY_MIN):
                            self._post_close_done[market] = local_date_str

        while not self._stop_event.is_set():
            now = self.clock.now()

            for market in self.config.enabled_markets_list:
                cal = self.calendars.get(market)
                if not cal:
                    continue

                is_open = cal.is_market_open(now)

                if is_open:
                    # If open, check if time since last scan >= effective interval
                    last_started = self.state.last_scan_started_at.get(market)
                    eff_int = self.state.effective_interval_sec(market)
                    if (
                        last_started is None
                        or (now - last_started).total_seconds() >= eff_int
                    ):
                        await self.execute_scan(market)
                else:
                    # Market closed: check post-close scan trigger
                    local_dt = cal.to_exchange_local(now)
                    local_date_str = local_dt.strftime("%Y-%m-%d")

                    if cal.calendar.is_session(local_date_str):
                        sess = cal.calendar.date_to_session(local_date_str)
                        close_ts = cal.calendar.session_close(sess)
                        post_close_target = close_ts + timedelta(
                            minutes=self.config.MARKET_CLOSE_SCAN_DELAY_MIN
                        )

                        now_ts = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
                        if (
                            now_ts >= post_close_target
                            and self._post_close_done.get(market) != local_date_str
                        ):
                            logger.info(
                                f"[{market}] Triggering scheduled post-close scan "
                                f"({self.config.MARKET_CLOSE_SCAN_DELAY_MIN}m after close)..."
                            )
                            if await self.execute_scan(market):
                                self._post_close_done[market] = local_date_str

            # Sleep 15s or until manual trigger/stop
            try:
                await asyncio.wait_for(self._trigger_event.wait(), timeout=15.0)
                self._trigger_event.clear()
            except asyncio.TimeoutError:
                pass

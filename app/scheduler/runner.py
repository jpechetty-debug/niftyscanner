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
        self._reserved_market: Optional[str] = None
        self._stopping = False

    async def start(self) -> None:
        """Start the background scheduler task."""
        self._stopping = False
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="scanner_scheduler_loop")
        logger.info(f"Background screening scheduler started for markets: {self.config.enabled_markets_list}")

    async def stop(self) -> None:
        """Signal the scheduler to stop and await termination."""
        logger.info("Stopping screening scheduler...")
        self._stopping = True
        self._stop_event.set()
        self._trigger_event.set()
        tasks = list(self._bg_tasks)
        if self._task:
            tasks.append(self._task)
        if tasks:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, Exception):
                    logger.error(f"Scheduler task failed: {outcome}")
        logger.info("Screening scheduler stopped.")

    async def execute_scan(self, market: str = "NSE") -> bool:
        """Execute a single scan for a specific market in a worker thread."""
        m = market.upper()
        scanner = self.scanners.get(m)
        universe = self.universes.get(m)

        if not scanner or not universe:
            logger.error(f"No scanner or universe registered for market {m}")
            return False

        lock = self.state.scan_lock
        if self._reserved_market is not None or lock.locked():
            return False

        async with lock:
            self.state.is_scanning[m] = True
            now = self.clock.now()
            self.state.last_scan_started_at[m] = now

            try:
                constituents = await asyncio.to_thread(universe.load)
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
                        data_as_of=getattr(scanner, "data_as_of", ""),
                        persist=False,
                    )
                    await asyncio.to_thread(self.state.persist_last_scan, m)
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
                self.state.last_scan_completed_at[m] = self.clock.now()
                self.state.is_scanning[m] = False

    def can_trigger_manual_refresh(self, market: str = "NSE") -> Tuple[bool, Optional[int]]:
        """Check if manual refresh for a market is permitted under lock and cooldown."""
        m = market.upper()
        if self._stopping or self._reserved_market is not None or self.state.scan_lock.locked() or any(self.state.is_scanning.values()):
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

        self._reserved_market = m
        self.state.is_scanning[m] = True
        self.state.last_scan_started_at[m] = self.clock.now()
        task = asyncio.create_task(self._run_reserved_scan(m))
        self._bg_tasks.add(task)               # keep a ref so it isn't GC'd
        task.add_done_callback(self._bg_tasks.discard)
        return True, None

    def wake(self) -> None:
        """Re-evaluate scheduled deadlines after runtime settings change."""
        self._trigger_event.set()

    async def _run_reserved_scan(self, market: str) -> bool:
        """Transfer the synchronous manual reservation to the shared scan lock."""
        self._reserved_market = None
        try:
            return await self.execute_scan(market)
        finally:
            self.state.is_scanning[market] = False

    async def _run_loop(self) -> None:
        """Main scheduler loop enforcing startup scans, market-hours gating, and post-close runs."""
        # 1. Mandatory scan at startup for all enabled markets
        logger.info(f"Executing startup scans for {self.config.enabled_markets_list}...")
        for market in self.config.enabled_markets_list:
            if self._stop_event.is_set():
                return
            if self.universes.get(market):
                await self.execute_scan(market)
                cal = self.calendars.get(market)
                if cal and not cal.is_market_open(self.clock.now()):
                    now = self.clock.now()
                    local_date_str = cal.to_exchange_local(now).strftime("%Y-%m-%d")
                    if cal.calendar.is_session(local_date_str):
                        close_ts = cal.calendar.session_close(cal.calendar.date_to_session(local_date_str))
                        target = close_ts + timedelta(minutes=self.config.MARKET_CLOSE_SCAN_DELAY_MIN)
                        completed = self.state.last_scan_completed_at.get(market)
                        if completed is not None and completed >= target:
                            self._post_close_done[market] = local_date_str

        while not self._stop_event.is_set():
            for market in self.config.enabled_markets_list:
                if self._stop_event.is_set():
                    return
                now = self.clock.now()
                cal = self.calendars.get(market)
                if not cal:
                    continue

                is_open = cal.is_market_open(now)

                if is_open:
                    # Measure the interval from completion, including failed scans
                    last_completed = self.state.last_scan_completed_at.get(market)
                    eff_int = self.state.get_effective_interval_sec(market)
                    if (
                        last_completed is None
                        or (now - last_completed).total_seconds() >= eff_int
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
                        completed = self.state.last_scan_completed_at.get(market)
                        if completed is not None and completed >= post_close_target:
                            self._post_close_done[market] = local_date_str

                        now_ts = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
                        if (
                            now_ts >= post_close_target
                            and self._post_close_done.get(market) != local_date_str
                        ):
                            logger.info(
                                f"[{market}] Triggering scheduled post-close scan "
                                f"({self.config.MARKET_CLOSE_SCAN_DELAY_MIN}m after close)..."
                            )
                            await self.execute_scan(market)
                            completed = self.state.last_scan_completed_at.get(market)
                            if completed is not None and completed >= post_close_target:
                                self._post_close_done[market] = local_date_str

            # Sleep 15s or until manual trigger/stop
            try:
                await asyncio.wait_for(self._trigger_event.wait(), timeout=15.0)
                self._trigger_event.clear()
            except asyncio.TimeoutError:
                pass

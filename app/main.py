"""FastAPI application initialization with multi-market lifespan scheduler."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Dict, Optional, Union
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api.routes import router
from app.cache.pe_cache import PECache
from app.core.circuit_breaker import CircuitBreaker
from app.core.config import Settings, load_settings
from app.core.interfaces import Clock, MarketDataProvider, Universe
from app.market.calendar import MarketCalendar
from app.market.clock import SystemClock
from app.providers.yfinance_provider import YFinanceProvider
from app.scheduler.runner import Scheduler
from app.services.scanner import StockScannerService
from app.services.state import ScanStateManager
from app.performance.service import PerformanceService
from app.performance.provider import YahooOutcomeProvider
from app.universe.nse import NSEUniverse
from app.universe.nyse import NYSEUniverse


def create_app(
    config: Optional[Settings] = None,
    provider: Optional[MarketDataProvider] = None,
    universe_loader: Optional[Union[Universe, Dict[str, Universe]]] = None,
    clock: Optional[Clock] = None,
    calendar: Optional[Union[MarketCalendar, Dict[str, MarketCalendar]]] = None,
    start_scheduler: bool = True,
) -> FastAPI:
    """Create and configure FastAPI application instance with multi-market support."""
    cfg = config or load_settings()
    clk = clock or SystemClock()

    # Multi-market calendars
    if isinstance(calendar, dict):
        calendars = {k.upper(): v for k, v in calendar.items()}
    elif calendar is not None:
        calendars = {cfg.enabled_markets_list[0]: calendar}
    else:
        calendars = {m: MarketCalendar(market=m) for m in cfg.enabled_markets_list}

    download_breakers: Dict[str, CircuitBreaker] = {}
    pe_breakers: Dict[str, CircuitBreaker] = {}
    pe_cache = PECache(ttl_hours=cfg.PE_CACHE_TTL_HOURS, clock=clk)
    
    from app.cache.bar_cache import BarCache
    bar_cache = BarCache(clock=clk)
    # Multi-market universes
    universes: Dict[str, Universe] = {}
    if isinstance(universe_loader, dict):
        universes = {k.upper(): v for k, v in universe_loader.items()}
    elif universe_loader is not None:
        universes = {cfg.enabled_markets_list[0]: universe_loader}
    else:
        if "NSE" in cfg.enabled_markets_list:
            universes["NSE"] = NSEUniverse(file_path=cfg.NSE_UNIVERSE_PATH)
        if "NYSE" in cfg.enabled_markets_list:
            universes["NYSE"] = NYSEUniverse(file_path=cfg.NYSE_UNIVERSE_PATH, config=cfg)

    # Multi-market scanner services
    scanner_services: Dict[str, StockScannerService] = {}
    
    # We will pass a provider instance directly if one was passed in via testing.
    # Otherwise, we create one for each market.
    for m in cfg.enabled_markets_list:
        cal = calendars.get(m, MarketCalendar(market=m))
        
        # Instantiate per-market breakers
        download_breakers[m] = CircuitBreaker(
            name=f"download_{m.lower()}",
            threshold=cfg.BREAKER_THRESHOLD,
            cooldown_sec=cfg.BREAKER_COOLDOWN_SEC,
            clock=clk,
        )
        pe_breakers[m] = CircuitBreaker(
            name=f"pe_{m.lower()}",
            threshold=cfg.BREAKER_THRESHOLD,
            cooldown_sec=cfg.BREAKER_COOLDOWN_SEC,
            clock=clk,
        )
        
        m_provider = provider or YFinanceProvider(
            config=cfg,
            clock=clk,
            download_breaker=download_breakers[m],
            pe_breaker=pe_breakers[m],
            pe_cache=pe_cache,
            bar_cache=bar_cache,
        )
        
        scanner_services[m] = StockScannerService(
            config=cfg,
            provider=m_provider,
            calendar=cal,
            clock=clk,
        )

    state_manager = ScanStateManager(
        config=cfg,
        download_breakers=download_breakers,
        pe_breakers=pe_breakers,
        calendars=calendars,
        clock=clk,
    )

    performance = PerformanceService(cfg, clk, calendars["NSE"],
        YahooOutcomeProvider(cfg.PERFORMANCE_TIMEOUT_SEC, cfg.PERFORMANCE_DATA_DIR)) if "NSE" in calendars else None
    scheduler = Scheduler(
        config=cfg,
        scanner_services=scanner_services,
        state_manager=state_manager,
        universes=universes,
        calendars=calendars,
        clock=clk,
        performance_service=performance,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: initialize state and scheduler
        logger.info(f"Starting FastAPI stock screener for markets: {cfg.enabled_markets_list}")
        if start_scheduler:
            await scheduler.start()
        yield
        # Shutdown
        logger.info("Shutting down FastAPI application...")
        if start_scheduler:
            await scheduler.stop()

    app = FastAPI(
        title="Stock Screener API",
        description="Local-first stock screener service for Nifty 500 and NYSE.",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Allow local CORS for Streamlit frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Attach shared components to app.state
    app.state.config = cfg
    app.state.clock = clk
    app.state.calendars = calendars
    app.state.state_manager = state_manager
    app.state.scheduler = scheduler
    app.state.performance = performance

    app.include_router(router)
    from app.performance.api import router as performance_router
    app.include_router(performance_router)
    return app


# Default application instance for uvicorn run
app = create_app()

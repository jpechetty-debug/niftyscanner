"""Scan orchestration service coordinating Stage 1 (price/volume/RSI) and Stage 2 (P/E)."""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple
from loguru import logger

from app.core.config import Settings
from app.core.filters import (
    apply_stage1_filters,
    apply_stage2_pe_filter,
)
from app.core.indicators import IndicatorResult, compute_indicators
from app.core.interfaces import (
    Clock,
    MarketDataProvider,
    ScanResultItem,
    UniverseSymbol,
)
from app.core.outcomes import (
    FailedSymbolItem,
    FailureCode,
    FunnelCounts,
    FunnelTracker,
    Stage,
)
from app.core.ranking import calculate_composite_score, rank_results
from app.market.calendar import MarketCalendar
from app.market.clock import SystemClock


class StockScannerService:
    """Orchestrates screening pipeline execution across markets."""

    def __init__(
        self,
        config: Settings,
        provider: MarketDataProvider,
        calendar: Optional[MarketCalendar] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.calendar = calendar or MarketCalendar(market=config.ENABLED_MARKETS.split(",")[0].strip())
        self.clock = clock or SystemClock()

    def run_scan(
        self, constituents: List[UniverseSymbol]
    ) -> Tuple[List[ScanResultItem], FunnelCounts, List[FailedSymbolItem], int, float, bool]:
        """Execute full screening pipeline.

        Returns:
            Tuple of:
            - Ranked results list
            - Funnel counts
            - Failed symbols list
            - Request count
            - Scan duration in seconds
        """
        start_time = time.perf_counter()
        tracker = FunnelTracker(universe=len(constituents))
        total_requests = 0

        symbol_map: Dict[str, UniverseSymbol] = {s.ticker: s for s in constituents}
        tickers = list(symbol_map.keys())

        current_dt = self.clock.now()

        # ======================================================================
        # Stage 1: Batch Download & Price / Volume / RSI Analysis
        # ======================================================================
        logger.info(f"Starting Stage 1 download for {len(tickers)} symbols...")
        bars_dict, req_count, download_fails = self.provider.download_bars(tickers)
        total_requests += req_count

        for fail in download_fails:
            tracker.record_failure(fail.ticker, fail.stage, fail.code, fail.message)

        tracker.fetched = len(bars_dict)
        logger.info(f"Successfully fetched bar history for {tracker.fetched} symbols.")

        stage1_survivors: Dict[str, IndicatorResult] = {}

        for ticker, df in bars_dict.items():
            indicator, fail_code, fail_msg = compute_indicators(
                df=df,
                current_dt=current_dt,
                calendar=self.calendar,
                rsi_period=self.config.RSI_PERIOD,
                volume_lookback=self.config.VOLUME_LOOKBACK,
                min_bars=self.config.MIN_BARS,
                max_bar_age_sessions=self.config.MAX_BAR_AGE_SESSIONS,
            )

            if fail_code is not None:
                tracker.record_failure(
                    ticker=ticker,
                    stage=Stage.INDICATORS,
                    code=fail_code,
                    message=fail_msg or "Indicator calculation failed",
                )
                continue

            assert indicator is not None
            # Apply Stage 1 business logic filters (RSI, Volume ratio)
            if apply_stage1_filters(indicator, self.config, tracker):
                stage1_survivors[ticker] = indicator

        logger.info(
            f"Stage 1 complete: {len(stage1_survivors)} symbols passed RSI & Volume thresholds."
        )

        # ======================================================================
        # Stage 2: Trailing P/E Evaluation (Survivors Only)
        # ======================================================================
        results: List[ScanResultItem] = []

        if stage1_survivors:
            survivor_tickers = list(stage1_survivors.keys())
            logger.info(f"Starting Stage 2 P/E fetch for {len(survivor_tickers)} survivors...")
            pe_dict, pe_reqs, pe_fails = self.provider.fetch_pe_batch(survivor_tickers)
            total_requests += pe_reqs

            for fail in pe_fails:
                tracker.record_failure(fail.ticker, fail.stage, fail.code, fail.message)

            for ticker, pe_value in pe_dict.items():
                if apply_stage2_pe_filter(pe_value, self.config, tracker):
                    indicator = stage1_survivors[ticker]
                    u_sym = symbol_map[ticker]

                    score = calculate_composite_score(
                        volume_ratio=indicator.volume_ratio,
                        rsi=indicator.rsi,
                        pe=pe_value,
                        config=self.config,
                    )

                    results.append(
                        ScanResultItem(
                            ticker=ticker,
                            name=u_sym.company_name,
                            market=u_sym.market,
                            price=indicator.price,
                            pe=pe_value,
                            rsi=indicator.rsi,
                            volume=indicator.volume,
                            avg_volume_20d=indicator.avg_volume_20d,
                            volume_ratio=indicator.volume_ratio,
                            score=score,
                            session_partial=indicator.session_partial,
                            rsi_trend=indicator.rsi_trend,
                            bar_date=indicator.bar_date.isoformat(),
                        )
                    )

        # ======================================================================
        # Scoring & Deterministic Ranking
        # ======================================================================
        ranked_results = rank_results(results)
        scan_seconds = round(time.perf_counter() - start_time, 2)
        logger.info(
            f"Screening complete: {len(ranked_results)} qualified stocks identified in {scan_seconds}s "
            f"across {total_requests} requests."
        )

        # Check if scan is considered successful or failed per Section 13
        is_success = True
        fetch_ratio = tracker.fetched / max(len(constituents), 1)
        if fetch_ratio < self.config.SCAN_MIN_FETCH_RATIO:
            logger.error(
                f"Scan failed: fetch ratio {fetch_ratio:.2f} is below SCAN_MIN_FETCH_RATIO "
                f"({self.config.SCAN_MIN_FETCH_RATIO})"
            )
            is_success = False

        if any(f.code == FailureCode.CIRCUIT_OPEN for f in tracker.failed_symbols):
            logger.error("Scan failed: circuit breaker opened during execution.")
            is_success = False

        return (
            ranked_results,
            tracker.to_counts(),
            tracker.failed_symbols,
            total_requests,
            scan_seconds,
            is_success,
        )

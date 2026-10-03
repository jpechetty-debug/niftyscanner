"""Live market data provider using yfinance with dual circuit breakers and P/E caching.

Notes:
- yfinance is unofficial and intended for personal use only.
- Market data is delayed and not real-time.
- Chunking reduces overhead, not request count.
"""

from __future__ import annotations

import concurrent.futures
import math
import random
from typing import Dict, List, Optional, Tuple
from loguru import logger
import pandas as pd
import yfinance as yf

from app.cache.pe_cache import PECache
from app.cache.bar_cache import BarCache
from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.core.config import Settings
from app.core.interfaces import Clock, MarketDataProvider
from app.core.outcomes import FailedSymbolItem, FailureCode, Stage
from app.market.clock import SystemClock


class YFinanceProvider(MarketDataProvider):
    """Fetches OHLCV daily bars and P/E ratios using yfinance with resilience protections."""

    def __init__(
        self,
        config: Settings,
        clock: Optional[Clock] = None,
        download_breaker: Optional[CircuitBreaker] = None,
        pe_breaker: Optional[CircuitBreaker] = None,
        pe_cache: Optional[PECache] = None,
        bar_cache: Optional[BarCache] = None,
    ) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self.total_requests = 0

        self.download_breaker = download_breaker or CircuitBreaker(
            name="download",
            threshold=config.BREAKER_THRESHOLD,
            cooldown_sec=config.BREAKER_COOLDOWN_SEC,
            clock=self.clock,
        )
        self.pe_breaker = pe_breaker or CircuitBreaker(
            name="pe",
            threshold=config.BREAKER_THRESHOLD,
            cooldown_sec=config.BREAKER_COOLDOWN_SEC,
            clock=self.clock,
        )
        self.pe_cache = pe_cache or PECache(
            ttl_hours=config.PE_CACHE_TTL_HOURS,
            clock=self.clock,
        )
        self.bar_cache = bar_cache or BarCache(clock=self.clock)

    @staticmethod
    def _fail_all(
        tickers: List[str], stage: Stage, code: FailureCode, message: str
    ) -> List[FailedSymbolItem]:
        """One failure per affected symbol so funnel.failed reflects real symbol counts."""
        return [FailedSymbolItem(ticker=t, stage=stage, code=code, message=message) for t in tickers]

    def _extract_ticker_df(self, chunk_df: pd.DataFrame, ticker: str) -> Optional[pd.DataFrame]:
        """Extract a single ticker's DataFrame handling MultiIndex and flat columns."""
        if chunk_df is None or chunk_df.empty:
            return None

        if isinstance(chunk_df.columns, pd.MultiIndex):
            levels = chunk_df.columns.levels
            # Check level 1 (e.g. ('Close', 'INFY.NS'))
            if len(levels) > 1 and ticker in chunk_df.columns.get_level_values(1):
                sub_df = chunk_df.xs(ticker, axis=1, level=1)
                return sub_df.copy()
            # Check level 0 (e.g. ('INFY.NS', 'Close'))
            elif len(levels) > 0 and ticker in chunk_df.columns.get_level_values(0):
                sub_df = chunk_df.xs(ticker, axis=1, level=0)
                return sub_df.copy()
            else:
                return None
        else:
            # Flat columns - single ticker download
            return chunk_df.copy()

    def download_bars(
        self, tickers: List[str]
    ) -> Tuple[Dict[str, pd.DataFrame], int, List[FailedSymbolItem]]:
        """Download daily bars in sequential chunks with delay, jitter, and circuit breaker."""
        results: Dict[str, pd.DataFrame] = {}
        failures: List[FailedSymbolItem] = []
        request_count = 0

        # Check circuit breaker before initiating download
        if not self.download_breaker.is_available():
            logger.error("Download circuit breaker is OPEN. Aborting price scan.")
            failures.extend(
                self._fail_all(
                    tickers, Stage.DOWNLOAD, FailureCode.CIRCUIT_OPEN,
                    f"Download circuit breaker is {self.download_breaker.state.value}. Aborting scan.",
                )
            )
            return results, 0, failures

        chunk_size = self.config.CHUNK_SIZE
        chunks = [tickers[i : i + chunk_size] for i in range(0, len(tickers), chunk_size)]

        for idx, chunk in enumerate(chunks):
            # Check circuit breaker between chunks (chunk 0 was already checked above;
            # a second is_available() call would consume the HALF_OPEN trial and deadlock)
            if idx > 0 and not self.download_breaker.is_available():
                logger.error("Download circuit breaker tripped OPEN mid-scan. Aborting remaining chunks.")
                remaining = [t for c in chunks[idx:] for t in c]
                failures.extend(
                    self._fail_all(
                        remaining, Stage.DOWNLOAD, FailureCode.CIRCUIT_OPEN,
                        f"Download circuit breaker opened during chunk {idx+1}/{len(chunks)}.",
                    )
                )
                break

            # Chunk delay with jitter between chunks
            if idx > 0:
                jitter = random.uniform(
                    self.config.CHUNK_DELAY_MIN_SEC, self.config.CHUNK_DELAY_MAX_SEC
                )
                self.clock.sleep(jitter)

            chunk_reqs = len(chunk)
            request_count += chunk_reqs
            self.total_requests += chunk_reqs

            chunk_df = None
            last_err = None

            needs_full = []
            needs_delta = []
            
            for t in chunk:
                if self.bar_cache.get_bars(t) is not None:
                    needs_delta.append(t)
                else:
                    needs_full.append(t)
                    
            chunk_df_full = None
            chunk_df_delta = None
            last_err = None

            for attempt in range(self.config.MAX_RETRIES):
                try:
                    if needs_full:
                        chunk_df_full = yf.download(
                            tickers=needs_full,
                            period="6mo",
                            interval="1d",
                            auto_adjust=True,
                            threads=self.config.DOWNLOAD_THREADS,
                            progress=False,
                        )
                    if needs_delta:
                        chunk_df_delta = yf.download(
                            tickers=needs_delta,
                            period="5d",
                            interval="1d",
                            auto_adjust=True,
                            threads=self.config.DOWNLOAD_THREADS,
                            progress=False,
                        )
                    # Check if empty frame was returned without raising
                    if (needs_full and (chunk_df_full is None or chunk_df_full.empty)) or \
                       (needs_delta and (chunk_df_delta is None or chunk_df_delta.empty)):
                        last_err = "Empty DataFrame returned from yf.download"
                        backoff = (2**attempt) + random.uniform(0.1, 0.5)
                        self.clock.sleep(backoff)
                        continue
                    break
                except Exception as e:
                    last_err = str(e)
                    backoff = (2**attempt) + random.uniform(0.1, 0.5)
                    self.clock.sleep(backoff)

            # Check for systemic chunk failure
            if (needs_full and (chunk_df_full is None or chunk_df_full.empty)) or \
               (needs_delta and (chunk_df_delta is None or chunk_df_delta.empty)):
                self.download_breaker.record_systemic_failure(
                    f"Chunk {idx+1} failed after {self.config.MAX_RETRIES} retries: {last_err}"
                )
                failures.extend(
                    self._fail_all(
                        chunk, Stage.DOWNLOAD,
                        FailureCode.EMPTY_CHUNK if "Empty" in str(last_err) else FailureCode.DOWNLOAD_ERROR,
                        f"Chunk {idx+1}/{len(chunks)} ({len(chunk)} symbols) failed: {last_err}",
                    )
                )
                continue

            all_nan_count = 0
            for ticker in chunk:
                source_df = chunk_df_delta if ticker in needs_delta else chunk_df_full
                t_df = self._extract_ticker_df(source_df, ticker)
                if t_df is None or ("Close" in t_df.columns and t_df["Close"].isna().all()):
                    all_nan_count += 1

            if len(chunk) > 2 and (all_nan_count / len(chunk)) > 0.50:
                self.download_breaker.record_systemic_failure(
                    f"Chunk {idx+1} had {all_nan_count}/{len(chunk)} all-NaN symbols (>50%)"
                )
            else:
                self.download_breaker.record_success()

            # Process individual symbols in chunk
            for ticker in chunk:
                source_df = chunk_df_delta if ticker in needs_delta else chunk_df_full
                ticker_df = self._extract_ticker_df(source_df, ticker)
                if ticker_df is None or ticker_df.empty:
                    failures.append(
                        FailedSymbolItem(
                            ticker=ticker,
                            stage=Stage.DOWNLOAD,
                            code=FailureCode.DELISTED,
                            message="No data returned in chunk, possibly delisted or symbol mismatch",
                        )
                    )
                elif "Close" in ticker_df.columns and ticker_df["Close"].isna().all():
                    failures.append(
                        FailedSymbolItem(
                            ticker=ticker,
                            stage=Stage.DOWNLOAD,
                            code=FailureCode.DELISTED,
                            message="All Close prices returned are NaN, possibly delisted",
                        )
                    )
                else:
                    if ticker in needs_delta and not self.bar_cache.is_consistent(ticker, ticker_df):
                        # History was retroactively adjusted (split/dividend): re-pull full history.
                        logger.info(f"{ticker}: price adjustment detected, refetching full history")
                        self.bar_cache.invalidate(ticker)
                        try:
                            request_count += 1
                            full = yf.download(
                                tickers=[ticker], period="6mo", interval="1d",
                                auto_adjust=True, progress=False,
                            )
                            refetched = self._extract_ticker_df(full, ticker)
                            if refetched is not None and not refetched.empty:
                                ticker_df = refetched
                        except Exception as e:
                            logger.warning(f"{ticker}: full refetch failed: {e}")
                    ticker_df = self.bar_cache.update_bars(ticker, ticker_df)
                    results[ticker] = ticker_df

        return results, request_count, failures

    def _fetch_single_pe(
        self, ticker: str
    ) -> Tuple[str, Optional[float], Optional[FailureCode], Optional[str], bool]:
        """Fetch trailing P/E for a single ticker via Ticker.info.

        Returns:
            Tuple of (ticker, pe_float, fail_code, fail_msg, is_systemic_failure)
        """
        try:
            t = yf.Ticker(ticker)
            info = t.info

            if not isinstance(info, dict) or "trailingPE" not in info:
                return ticker, None, FailureCode.MISSING_PE, "trailingPE key absent in Ticker.info", False

            raw_pe = info.get("trailingPE")
            from app.core.filters import evaluate_pe_value
            pe_val, code, msg = evaluate_pe_value(raw_pe)
            
            if code is not None:
                return ticker, None, code, msg, False
                
            return ticker, pe_val, None, None, False

        except Exception as e:
            # Check for systemic error indicators (rate limit / network issues)
            err_str = str(e).lower()
            is_systemic = any(s in err_str for s in ["rate", "429", "timeout", "crumb", "unauthorized", "connection"])
            return ticker, None, FailureCode.PE_FETCH_FAILED, f"Error fetching Ticker.info: {e}", is_systemic

    def fetch_pe_batch(
        self, tickers: List[str]
    ) -> Tuple[Dict[str, float], int, List[FailedSymbolItem]]:
        """Fetch trailing P/E using cache, thread pool, and PE circuit breaker."""
        results: Dict[str, float] = {}
        failures: List[FailedSymbolItem] = []
        request_count = 0

        tickers_to_fetch: List[str] = []

        # 1. Check in-memory P/E cache first
        for ticker in tickers:
            hit, cached_val, cached_code = self.pe_cache.get(ticker)
            if hit:
                if cached_code is not None:
                    failures.append(
                        FailedSymbolItem(
                            ticker=ticker,
                            stage=Stage.PE,
                            code=cached_code,
                            message="Cached missing P/E",
                        )
                    )
                elif cached_val is not None:
                    results[ticker] = cached_val
            else:
                tickers_to_fetch.append(ticker)

        if not tickers_to_fetch:
            return results, 0, failures

        # 2. Check PE circuit breaker before network calls
        if not self.pe_breaker.is_available():
            logger.error("P/E circuit breaker is OPEN. Aborting fundamental fetch.")
            failures.extend(
                self._fail_all(
                    tickers_to_fetch, Stage.PE, FailureCode.CIRCUIT_OPEN,
                    f"P/E circuit breaker is {self.pe_breaker.state.value}. Aborting P/E stage.",
                )
            )
            return results, 0, failures

        workers = min(self.config.PE_WORKERS, len(tickers_to_fetch))
        request_count = len(tickers_to_fetch)
        self.total_requests += request_count

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(self._fetch_single_pe, ticker) for ticker in tickers_to_fetch]
            for future in concurrent.futures.as_completed(futures):
                ticker, pe_val, fail_code, fail_msg, is_systemic = future.result()

                if fail_code is not None:
                    if is_systemic:
                        self.pe_breaker.record_systemic_failure(fail_msg or "Systemic P/E error")
                    else:
                        # Endpoint responded (MISSING_PE / INVALID_PE): not a systemic fault.
                        self.pe_breaker.record_success()
                    if fail_code == FailureCode.MISSING_PE:
                        # MISSING_PE is cached (Section 14)
                        self.pe_cache.set_missing_pe(ticker)
                    # PE_FETCH_FAILED is NEVER cached (Section 14)

                    failures.append(
                        FailedSymbolItem(
                            ticker=ticker,
                            stage=Stage.PE,
                            code=fail_code,
                            message=fail_msg or "P/E acquisition failure",
                        )
                    )
                elif pe_val is not None:
                    self.pe_breaker.record_success()
                    self.pe_cache.set_valid_pe(ticker, pe_val)
                    results[ticker] = pe_val

        return results, request_count, failures

"""Live market data provider using yfinance with dual circuit breakers and P/E caching.

Notes:
- yfinance is unofficial and intended for personal use only.
- Market data is delayed and not real-time.
- Chunking reduces overhead, not request count.
"""

from __future__ import annotations

import concurrent.futures
import threading
import random
from typing import Dict, List, Optional, Tuple
from loguru import logger
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from app.cache.pe_cache import PECache
from app.cache.bar_cache import BarCache
from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.core.config import Settings
from app.core.fundamentals import NonPositiveEarnings, confirmed_nonpositive_earnings
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
        self._pe_lock = threading.Lock()
        self._pe_abort = threading.Event()

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
        self.pe_cache = pe_cache if pe_cache is not None else PECache(
            ttl_hours=config.PE_CACHE_TTL_HOURS,
            clock=self.clock,
        )
        self.bar_cache = bar_cache or BarCache(clock=self.clock)

    @staticmethod
    def _fail_all(
        tickers: List[str], stage: Stage, code: FailureCode, message: str
    ) -> List[FailedSymbolItem]:
        """Aggregate a systemic failure while retaining the affected-symbol count."""
        return [FailedSymbolItem(ticker="*", stage=stage, code=code, message=message, affected_count=len(tickers))]

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

    @staticmethod
    def _is_systemic_error(error: Exception) -> bool:
        """Recognize rate limits and transport errors without retrying bad inputs."""
        return isinstance(error, (YFRateLimitError, TimeoutError, ConnectionError, OSError)) or any(
            text in str(error).lower() for text in ("rate limit", "429", "timeout", "timed out", "crumb", "unauthorized", "connection", "network")
        )

    def _download_group(self, tickers: List[str], period: str) -> Tuple[Optional[pd.DataFrame], int, Optional[FailureCode], Optional[str]]:
        """Retry one download group; count ticker requests on every attempt."""
        count = 0
        code = FailureCode.DOWNLOAD_ERROR
        message = "Download failed"
        for attempt in range(self.config.MAX_RETRIES):
            if not self.download_breaker.is_available():
                return None, count, FailureCode.CIRCUIT_OPEN, "Download circuit breaker is open"
            try:
                count += len(tickers)
                self.total_requests += len(tickers)
                frame = yf.download(tickers=tickers, period=period, interval="1d",
                                    auto_adjust=True, threads=self.config.DOWNLOAD_THREADS, progress=False)
                if frame is None or frame.empty:
                    code, message = FailureCode.EMPTY_CHUNK, "Empty DataFrame returned from yf.download"
                else:
                    missing = sum(
                        (df is None or df.empty or "Close" not in df or df["Close"].isna().all())
                        for df in (self._extract_ticker_df(frame, ticker) for ticker in tickers)
                    )
                    if missing / len(tickers) <= 0.5:
                        self.download_breaker.record_success()
                        return frame, count, None, None
                    code, message = FailureCode.EMPTY_CHUNK, "More than 50% of symbols have no Close data"
            except Exception as error:
                code = FailureCode.THROTTLED if isinstance(error, YFRateLimitError) else FailureCode.DOWNLOAD_ERROR
                message = str(error)
                if not self._is_systemic_error(error):
                    self.download_breaker.record_success()
                    return None, count, code, message
            self.download_breaker.record_systemic_failure(message)
            if self.download_breaker.state == CircuitState.OPEN or attempt + 1 == self.config.MAX_RETRIES:
                break
            self.clock.sleep(2**attempt + random.uniform(0.1, 0.5))
        return None, count, code, message

    def download_bars(self, tickers: List[str]) -> Tuple[Dict[str, pd.DataFrame], int, List[FailedSymbolItem]]:
        """Download sequential chunks, aborting on a tripped breaker."""
        results: Dict[str, pd.DataFrame] = {}
        failures: List[FailedSymbolItem] = []
        count = 0
        chunks = [tickers[i:i + self.config.CHUNK_SIZE] for i in range(0, len(tickers), self.config.CHUNK_SIZE)]
        for idx, chunk in enumerate(chunks):
            if idx:
                self.clock.sleep(random.uniform(self.config.CHUNK_DELAY_MIN_SEC, self.config.CHUNK_DELAY_MAX_SEC))
            groups = [
                ([t for t in chunk if self.bar_cache.get_bars(t) is None], "6mo"),
                ([t for t in chunk if self.bar_cache.get_bars(t) is not None], "5d"),
            ]
            for group, period in groups:
                if not group:
                    continue
                frame, attempts, code, message = self._download_group(group, period)
                count += attempts
                if code is not None:
                    if code != FailureCode.CIRCUIT_OPEN:
                        failures.extend(self._fail_all(group, Stage.DOWNLOAD, code,
                            f"Chunk {idx+1}/{len(chunks)} ({len(group)} symbols): {message}"))
                else:
                    for ticker in group:
                        bars = self._extract_ticker_df(frame, ticker)
                        if bars is None or bars.empty or "Close" not in bars or bars["Close"].isna().all():
                            failures.append(FailedSymbolItem(ticker=ticker, stage=Stage.DOWNLOAD,
                                code=FailureCode.DELISTED, message="No data returned, possibly delisted or symbol mismatch"))
                            continue
                        if period == "5d" and not self.bar_cache.is_consistent(ticker, bars):
                            self.bar_cache.invalidate(ticker)
                            full, attempts, refetch_code, refetch_message = self._download_group([ticker], "6mo")
                            count += attempts
                            if refetch_code is not None:
                                if refetch_code != FailureCode.CIRCUIT_OPEN:
                                    failures.append(FailedSymbolItem(ticker=ticker, stage=Stage.DOWNLOAD,
                                        code=refetch_code, message=f"Adjustment refetch failed: {refetch_message}"))
                                if self.download_breaker.state == CircuitState.OPEN:
                                    break
                                continue
                            bars = self._extract_ticker_df(full, ticker)
                        results[ticker] = self.bar_cache.update_bars(ticker, bars)
                if self.download_breaker.state == CircuitState.OPEN or code == FailureCode.CIRCUIT_OPEN:
                    remaining = max(0, len(tickers) - len(results) - sum(f.affected_count for f in failures))
                    failures.append(FailedSymbolItem(ticker="*", stage=Stage.DOWNLOAD,
                        code=FailureCode.CIRCUIT_OPEN, affected_count=remaining,
                        message=f"Download circuit opened at chunk {idx+1}/{len(chunks)}; {remaining} symbols skipped"))
                    return results, count, failures
        return results, count, failures

    def _fetch_single_pe(
        self, ticker: str
    ) -> Tuple[str, float | NonPositiveEarnings | None, Optional[FailureCode], Optional[str], bool]:
        """Fetch trailing P/E for a single ticker via Ticker.info.

        Returns:
            Tuple of (ticker, P/E or confirmed earnings marker, failure code/message, systemic flag)
        """
        try:
            t = yf.Ticker(ticker)
            info = t.info

            if isinstance(info, dict):
                earnings = confirmed_nonpositive_earnings(info)
                if earnings is not None:
                    return ticker, earnings, None, None, False

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
            is_systemic = self._is_systemic_error(e)
            return ticker, None, FailureCode.PE_FETCH_FAILED, f"Error fetching Ticker.info: {e}", is_systemic

    def _fetch_pe_with_retries(self, ticker: str) -> Tuple[Tuple[str, float | NonPositiveEarnings | None, Optional[FailureCode], Optional[str], bool], int]:
        """Reserve each attempt under a lock, including the single half-open trial."""
        count = 0
        for attempt in range(self.config.MAX_RETRIES):
            with self._pe_lock:
                if self._pe_abort.is_set() or not self.pe_breaker.is_available():
                    return (ticker, None, FailureCode.CIRCUIT_OPEN, "P/E circuit is open", True), count
                count += 1
                self.total_requests += 1
            outcome = self._fetch_single_pe(ticker)
            _, _, _, message, systemic = outcome
            with self._pe_lock:
                if systemic:
                    self.pe_breaker.record_systemic_failure(message or "P/E transport error")
                elif self.pe_breaker.state != CircuitState.OPEN:
                    self.pe_breaker.record_success()
                opened = self.pe_breaker.state == CircuitState.OPEN
                if opened:
                    self._pe_abort.set()
            if not systemic or opened or attempt + 1 == self.config.MAX_RETRIES:
                return outcome, count
            self.clock.sleep(2**attempt + random.uniform(0.1, 0.5))
        raise RuntimeError("Unreachable retry state")

    def fetch_pe_batch(self, tickers: List[str]) -> Tuple[Dict[str, float | NonPositiveEarnings], int, List[FailedSymbolItem]]:
        """Fetch bounded batches; stop dispatch on breaker opening and retain failures."""
        results: Dict[str, float | NonPositiveEarnings] = {}
        failures: List[FailedSymbolItem] = []
        pending: List[str] = []
        count = 0
        for ticker in tickers:
            hit, value, code = self.pe_cache.get(ticker)
            if not hit:
                pending.append(ticker)
            elif code is not None:
                failures.append(FailedSymbolItem(ticker=ticker, stage=Stage.PE, code=code, message="Cached missing P/E"))
            elif value is not None:
                results[ticker] = value
        self._pe_abort.clear()
        cursor = 0
        completed = set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.PE_WORKERS) as pool:
            while cursor < len(pending):
                if self._pe_abort.is_set() or self.pe_breaker.state == CircuitState.OPEN:
                    self._pe_abort.set()
                    break
                width = 1 if self.pe_breaker.state == CircuitState.HALF_OPEN else self.config.PE_WORKERS
                batch = pending[cursor:cursor + width]
                cursor += len(batch)
                futures = [pool.submit(self._fetch_pe_with_retries, ticker) for ticker in batch]
                for future in concurrent.futures.as_completed(futures):
                    outcome, attempts = future.result()
                    count += attempts
                    ticker, value, code, message, _ = outcome
                    if code == FailureCode.CIRCUIT_OPEN:
                        continue
                    completed.add(ticker)
                    if code is not None:
                        failures.append(FailedSymbolItem(ticker=ticker, stage=Stage.PE, code=code, message=message or "P/E fetch failed"))
                        if code == FailureCode.MISSING_PE:
                            self.pe_cache.set_missing_pe(ticker)
                    elif value is not None:
                        self.pe_cache.set_valid_pe(ticker, value)
                        results[ticker] = value
                if self._pe_abort.is_set() or self.pe_breaker.state == CircuitState.OPEN:
                    self._pe_abort.set()
                    break
        if self._pe_abort.is_set():
            skipped = len(pending) - len(completed)
            failures.append(FailedSymbolItem(ticker="*", stage=Stage.PE, code=FailureCode.CIRCUIT_OPEN,
                affected_count=skipped, message=f"P/E circuit opened; {skipped} symbols skipped"))
        return results, count, failures

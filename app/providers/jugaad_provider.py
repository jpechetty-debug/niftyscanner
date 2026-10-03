"""Live market data provider using jugaad-data for NSE historical data.

This provider uses jugaad-data for robust historical data on the NSE,
and falls back to yfinance for P/E ratios since jugaad-data NSELive 
is often blocked by NSE anti-scraping mechanisms.
"""

from __future__ import annotations

import concurrent.futures
from datetime import timedelta
from typing import Dict, List, Optional, Tuple
from loguru import logger
import pandas as pd
import yfinance as yf

from jugaad_data.nse import stock_df

from app.cache.pe_cache import PECache
from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.core.config import Settings
from app.core.interfaces import Clock, MarketDataProvider
from app.core.outcomes import FailedSymbolItem, FailureCode, Stage
from app.market.clock import SystemClock


class JugaadProvider(MarketDataProvider):
    """Fetches OHLCV daily bars using jugaad-data and P/E ratios using yfinance."""

    def __init__(
        self,
        config: Settings,
        clock: Optional[Clock] = None,
        download_breaker: Optional[CircuitBreaker] = None,
        pe_breaker: Optional[CircuitBreaker] = None,
        pe_cache: Optional[PECache] = None,
    ) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self.total_requests = 0

        self.download_breaker = download_breaker or CircuitBreaker(
            name="download_jugaad",
            threshold=config.BREAKER_THRESHOLD,
            cooldown_sec=config.BREAKER_COOLDOWN_SEC,
            clock=self.clock,
        )
        self.pe_breaker = pe_breaker or CircuitBreaker(
            name="pe_jugaad",
            threshold=config.BREAKER_THRESHOLD,
            cooldown_sec=config.BREAKER_COOLDOWN_SEC,
            clock=self.clock,
        )
        self.pe_cache = pe_cache or PECache(
            ttl_hours=config.PE_CACHE_TTL_HOURS,
            clock=self.clock,
        )

    @staticmethod
    def _fail_all(
        tickers: List[str], stage: Stage, code: FailureCode, message: str
    ) -> List[FailedSymbolItem]:
        return [
            FailedSymbolItem(ticker=t, stage=stage, code=code, message=message)
            for t in tickers
        ]

    def _fetch_single_history(self, ticker: str, start_date, end_date) -> Optional[pd.DataFrame]:
        try:
            # jugaad-data expects NSE symbol without .NS
            symbol = ticker.replace(".NS", "")
            df = stock_df(symbol=symbol, from_date=start_date, to_date=end_date, series="EQ")
            if df.empty:
                return None
            
            # Format dataframe to match yfinance output style
            df = df.rename(columns={
                "DATE": "Date",
                "OPEN": "Open",
                "HIGH": "High",
                "LOW": "Low",
                "CLOSE": "Close",
                "VOLUME": "Volume",
            })
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date").sort_index()
            return df
        except Exception as e:
            logger.debug(f"jugaad-data fetch failed for {ticker}: {e}")
            return None

    def download_bars(
        self, tickers: List[str]
    ) -> Tuple[Dict[str, pd.DataFrame], int, List[FailedSymbolItem]]:
        if self.download_breaker.state == CircuitState.OPEN:
            return {}, 0, self._fail_all(tickers, Stage.DOWNLOAD, FailureCode.DOWNLOAD_ERROR, "Circuit breaker OPEN")

        if self.download_breaker.state == CircuitState.HALF_OPEN:
            tickers = tickers[:1]

        end_date = self.clock.now().date()
        start_date = end_date - timedelta(days=90) # roughly 3 months
        
        results: Dict[str, pd.DataFrame] = {}
        failures: List[FailedSymbolItem] = []
        requests = 0

        # We must fetch sequentially or with a thread pool. Let's use a small thread pool.
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_ticker = {
                executor.submit(self._fetch_single_history, t, start_date, end_date): t
                for t in tickers
            }
            
            for future in concurrent.futures.as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                requests += 1
                try:
                    df = future.result()
                    if df is not None and not df.empty:
                        results[ticker] = df
                    else:
                        failures.append(
                            FailedSymbolItem(
                                ticker=ticker,
                                stage=Stage.DOWNLOAD,
                                code=FailureCode.DOWNLOAD_ERROR,
                                message="Empty or failed download",
                            )
                        )
                except Exception as e:
                    failures.append(
                        FailedSymbolItem(
                            ticker=ticker,
                            stage=Stage.DOWNLOAD,
                            code=FailureCode.DOWNLOAD_ERROR,
                            message=str(e),
                        )
                    )

        self.total_requests += requests
        
        if len(failures) == len(tickers) and len(tickers) > 0:
            self.download_breaker.record_systemic_failure()
        else:
            self.download_breaker.record_success()

        return results, requests, failures

    def fetch_pe_batch(
        self, tickers: List[str]
    ) -> Tuple[Dict[str, float], int, List[FailedSymbolItem]]:
        if self.pe_breaker.state == CircuitState.OPEN:
            return {}, 0, self._fail_all(tickers, Stage.PE, FailureCode.PE_FETCH_FAILED, "PE Circuit breaker OPEN")

        if self.pe_breaker.state == CircuitState.HALF_OPEN:
            tickers = tickers[:1]

        results: Dict[str, float] = {}
        failures: List[FailedSymbolItem] = []
        requests = 0
        missing = []

        for t in tickers:
            cached = self.pe_cache.get(t)
            if cached is not None:
                results[t] = cached
            else:
                missing.append(t)

        if not missing:
            return results, requests, failures

        # Fetch missing PEs using yfinance Ticker info as batch PE is hard
        def fetch_pe(ticker: str) -> Tuple[str, Optional[float], Optional[str]]:
            try:
                tkr = yf.Ticker(ticker)
                info = tkr.info
                if not info:
                    return ticker, None, "Empty info"
                pe = info.get("trailingPE")
                if pe is not None:
                    return ticker, float(pe), None
                return ticker, None, "No PE found"
            except Exception as e:
                return ticker, None, str(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_to_t = {executor.submit(fetch_pe, t): t for t in missing}
            for future in concurrent.futures.as_completed(future_to_t):
                requests += 1
                t, pe_val, err = future.result()
                if pe_val is not None:
                    results[t] = pe_val
                    self.pe_cache.set(t, pe_val)
                else:
                    code = FailureCode.PE_FETCH_FAILED if err != "No PE found" else FailureCode.MISSING_PE
                    failures.append(
                        FailedSymbolItem(ticker=t, stage=Stage.PE, code=code, message=err or "Unknown")
                    )

        self.total_requests += requests
        
        # If all missing failed with a fetch error (not just missing PE), breaker trip
        fetch_errors = [f for f in failures if f.code == FailureCode.PE_FETCH_FAILED]
        if missing and len(fetch_errors) == len(missing):
            self.pe_breaker.record_systemic_failure()
        else:
            self.pe_breaker.record_success()

        return results, requests, failures

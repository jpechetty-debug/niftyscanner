"""Providers for Macro Market Indicators (India VIX, Brent Crude, Nifty 50 / GIFT Proxy)."""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional, Protocol
from loguru import logger
import pandas as pd
import yfinance as yf

from app.core.config import Settings
from app.core.interfaces import Clock
from app.macro.models import MacroIndicatorItem, MacroOverviewResponse
from app.market.clock import SystemClock


class MacroDataProvider(Protocol):
    """Protocol for fetching macro indicators."""

    def get_macro_data(self) -> MacroOverviewResponse:
        """Retrieve aggregated macro market indicators."""
        ...


class LiveMacroProvider(MacroDataProvider):
    """Live macro indicator provider backed by yfinance with thread-safe caching."""

    def __init__(self, config: Settings, clock: Optional[Clock] = None) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self._lock = threading.Lock()
        self._cached_response: Optional[MacroOverviewResponse] = None
        self._cached_monotonic: float = 0.0

    def get_macro_data(self) -> MacroOverviewResponse:
        """Fetch macro indicators or return valid cached response."""
        now_mono = time.monotonic()
        with self._lock:
            if (
                self._cached_response is not None
                and (now_mono - self._cached_monotonic) < self.config.MACRO_CACHE_TTL_SEC
            ):
                return self._cached_response

        # Fetch outside lock to prevent blocking
        response = self._fetch_live_data()
        with self._lock:
            self._cached_response = response
            self._cached_monotonic = time.monotonic()
            return response

    def _fetch_live_data(self) -> MacroOverviewResponse:
        """Fetch quotes from yfinance and compute changes & regimes."""
        tickers = [
            self.config.MACRO_TICKER_NIFTY,
            self.config.MACRO_TICKER_VIX,
            self.config.MACRO_TICKER_BRENT,
        ]
        as_of_str = self.clock.now().isoformat()
        readings: Dict[str, Dict[str, Optional[float]]] = {}

        try:
            df = yf.download(
                tickers=tickers,
                period="5d",
                interval="1d",
                progress=False,
                auto_adjust=False,
            )
            if df is not None and not df.empty and "Close" in df:
                close_df = df["Close"]
                for t in tickers:
                    if t in close_df.columns:
                        series = close_df[t].dropna()
                        if len(series) >= 2:
                            curr = float(series.iloc[-1])
                            prev = float(series.iloc[-2])
                            readings[t] = {
                                "current": curr,
                                "prev": prev,
                                "change": curr - prev,
                                "change_pct": ((curr - prev) / prev) * 100 if prev else 0.0,
                            }
                        elif len(series) == 1:
                            curr = float(series.iloc[-1])
                            readings[t] = {
                                "current": curr,
                                "prev": curr,
                                "change": 0.0,
                                "change_pct": 0.0,
                            }
        except Exception as e:
            logger.warning(f"Batch macro download encountered issue: {e}")

        # If any ticker was missing from batch download, try individual fast_info
        for t in tickers:
            if t not in readings or readings[t]["current"] is None:
                try:
                    ticker_obj = yf.Ticker(t)
                    fi = ticker_obj.fast_info
                    last_price = getattr(fi, "last_price", None)
                    prev_close = getattr(fi, "previous_close", None)
                    if last_price is not None:
                        curr = float(last_price)
                        prev = float(prev_close) if prev_close else curr
                        chg = curr - prev
                        pct = (chg / prev) * 100 if prev else 0.0
                        readings[t] = {
                            "current": curr,
                            "prev": prev,
                            "change": chg,
                            "change_pct": pct,
                        }
                except Exception as ex:
                    logger.warning(f"Fast info fallback failed for {t}: {ex}")

        # Build indicator items
        indicators: List[MacroIndicatorItem] = []

        # 1. Nifty 50 (GIFT Proxy)
        nifty_data = readings.get(self.config.MACRO_TICKER_NIFTY, {})
        n_curr = nifty_data.get("current") or 0.0
        n_prev = nifty_data.get("prev")
        n_chg = nifty_data.get("change") or 0.0
        n_pct = nifty_data.get("change_pct") or 0.0

        if n_pct >= 0.5:
            n_sent, n_tone = f"Bullish (+{n_pct:.2f}%)", "positive"
        elif n_pct <= -0.5:
            n_sent, n_tone = f"Bearish ({n_pct:.2f}%)", "negative"
        else:
            n_sent, n_tone = f"Rangebound ({n_pct:+.2f}%)", "neutral"

        indicators.append(
            MacroIndicatorItem(
                symbol=self.config.MACRO_TICKER_NIFTY,
                name="Nifty 50 (GIFT Proxy)",
                current=round(n_curr, 2),
                prev_close=round(n_prev, 2) if n_prev else None,
                change=round(n_chg, 2),
                change_pct=round(n_pct, 2),
                unit="pts",
                sentiment=n_sent,
                tone=n_tone,
                as_of=as_of_str,
                synthetic=False,
            )
        )

        # 2. India VIX
        vix_data = readings.get(self.config.MACRO_TICKER_VIX, {})
        v_curr = vix_data.get("current") or 0.0
        v_prev = vix_data.get("prev")
        v_chg = vix_data.get("change") or 0.0
        v_pct = vix_data.get("change_pct") or 0.0

        if v_curr < self.config.MACRO_VIX_LOW_THRESHOLD and v_curr > 0:
            v_sent, v_tone = "Low Volatility (Favorable)", "positive"
        elif v_curr <= self.config.MACRO_VIX_HIGH_THRESHOLD:
            v_sent, v_tone = "Moderate Volatility", "neutral"
        else:
            v_sent, v_tone = "High Volatility (Caution)", "warning"

        indicators.append(
            MacroIndicatorItem(
                symbol=self.config.MACRO_TICKER_VIX,
                name="India VIX",
                current=round(v_curr, 2),
                prev_close=round(v_prev, 2) if v_prev else None,
                change=round(v_chg, 2),
                change_pct=round(v_pct, 2),
                unit="",
                sentiment=v_sent,
                tone=v_tone,
                as_of=as_of_str,
                synthetic=False,
            )
        )

        # 3. Brent Crude
        brent_data = readings.get(self.config.MACRO_TICKER_BRENT, {})
        b_curr = brent_data.get("current") or 0.0
        b_prev = brent_data.get("prev")
        b_chg = brent_data.get("change") or 0.0
        b_pct = brent_data.get("change_pct") or 0.0

        if b_pct >= 2.0:
            b_sent, b_tone = "Surging (Cost Pressure)", "warning"
        elif b_pct <= -2.0:
            b_sent, b_tone = "Softening (Favorable)", "positive"
        else:
            b_sent, b_tone = f"Stable (${b_curr:.2f})", "neutral"

        indicators.append(
            MacroIndicatorItem(
                symbol=self.config.MACRO_TICKER_BRENT,
                name="Brent Crude",
                current=round(b_curr, 2),
                prev_close=round(b_prev, 2) if b_prev else None,
                change=round(b_chg, 2),
                change_pct=round(b_pct, 2),
                unit="$",
                sentiment=b_sent,
                tone=b_tone,
                as_of=as_of_str,
                synthetic=False,
            )
        )

        # 4. Overall Market Regime Assessment
        if v_curr > self.config.MACRO_VIX_HIGH_THRESHOLD and n_pct < 0:
            regime = "Risk-Off · Elevated Volatility & Index Pressure"
            regime_tone = "warning"
        elif v_curr > self.config.MACRO_VIX_HIGH_THRESHOLD:
            regime = "Caution · Elevated Volatility"
            regime_tone = "warning"
        elif v_curr < self.config.MACRO_VIX_LOW_THRESHOLD and n_pct >= 0:
            regime = "Risk-On · Favorable Low Volatility"
            regime_tone = "positive"
        elif n_pct <= -1.0:
            regime = "Pullback · Broad Index Weakness"
            regime_tone = "negative"
        else:
            regime = "Balanced · Stable Macro Environment"
            regime_tone = "neutral"

        return MacroOverviewResponse(
            indicators=indicators,
            market_regime=regime,
            regime_tone=regime_tone,
            last_refreshed=as_of_str,
            synthetic=False,
        )


class FakeMacroProvider(MacroDataProvider):
    """Synthetic macro provider for tests and offline runs."""

    def __init__(self, clock: Optional[Clock] = None) -> None:
        self.clock = clock or SystemClock()

    def get_macro_data(self) -> MacroOverviewResponse:
        """Return synthetic macro fixtures."""
        as_of_str = self.clock.now().isoformat()
        indicators = [
            MacroIndicatorItem(
                symbol="^NSEI",
                name="Nifty 50 (GIFT Proxy)",
                current=22500.0,
                prev_close=22380.0,
                change=120.0,
                change_pct=0.54,
                unit="pts",
                sentiment="Bullish (+0.54%)",
                tone="positive",
                as_of=as_of_str,
                synthetic=True,
            ),
            MacroIndicatorItem(
                symbol="^INDIAVIX",
                name="India VIX",
                current=13.5,
                prev_close=14.0,
                change=-0.5,
                change_pct=-3.57,
                unit="",
                sentiment="Low Volatility (Favorable)",
                tone="positive",
                as_of=as_of_str,
                synthetic=True,
            ),
            MacroIndicatorItem(
                symbol="BZ=F",
                name="Brent Crude",
                current=78.20,
                prev_close=79.10,
                change=-0.90,
                change_pct=-1.14,
                unit="$",
                sentiment="Stable ($78.20)",
                tone="neutral",
                as_of=as_of_str,
                synthetic=True,
            ),
        ]
        return MacroOverviewResponse(
            indicators=indicators,
            market_regime="Synthetic: Risk-On · Low Volatility",
            regime_tone="positive",
            last_refreshed=as_of_str,
            synthetic=True,
        )

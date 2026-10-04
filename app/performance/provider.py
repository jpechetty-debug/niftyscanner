"""Outcome prices use Yahoo Open/Close, not dividend-adjusted Adj Close.

Yahoo's daily OHLC is split adjusted. Every entry/exit pair is fetched together
in a single vintage; persisted screener prices never enter the calculation.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Protocol
from pathlib import Path

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFPricesMissingError


class OutcomeDataUnavailable(ValueError):
    """An individual instrument has missing history; keep it unresolved."""


class OutcomePriceProvider(Protocol):
    def history(self, ticker: str, start: date, end: date) -> dict[str, dict]: ...


class YahooOutcomeProvider:
    def __init__(self, timeout: int = 10, cache_dir: str | None = None):
        self.timeout = timeout
        if cache_dir:
            yf.set_tz_cache_location(str(Path(cache_dir) / "yfinance-cache"))

    def history(self, ticker: str, start: date, end: date) -> dict[str, dict]:
        try:
            frame = yf.Ticker(ticker).history(start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(), interval="1d",
                auto_adjust=False, back_adjust=False, actions=True,
                repair=False, keepna=True, timeout=self.timeout, raise_errors=True)
        except YFPricesMissingError as error:
            raise OutcomeDataUnavailable(str(error)) from error
        if frame is None or frame.empty:
            raise OutcomeDataUnavailable(f"No outcome price history for {ticker}")
        if isinstance(frame.columns, pd.MultiIndex):
            frame = frame.xs(ticker, axis=1, level=-1)
        if not {"Open", "Close"}.issubset(frame.columns):
            raise OutcomeDataUnavailable(f"Invalid outcome price schema for {ticker}")
        if frame.index.duplicated().any():
            raise OutcomeDataUnavailable(f"Duplicate outcome sessions for {ticker}")
        prices = {}
        for session, row in frame.iterrows():
            values = {"open": float(row["Open"]), "close": float(row["Close"]),
                      "dividend": float(row.get("Dividends", 0)),
                      "split": float(row.get("Stock Splits", 0))}
            if not all(math.isfinite(values[k]) and values[k] > 0 for k in ("open", "close")):
                continue  # Missing data is not a zero return.
            prices[session.date().isoformat()] = values
        return prices

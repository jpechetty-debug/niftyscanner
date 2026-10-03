"""Unit tests for MarketDataProvider implementations and DataFrame extraction."""

import pandas as pd
import pytest

from app.core.config import Settings
from app.market.clock import FakeClock
from app.providers.fake_provider import FakeProvider
from app.providers.yfinance_provider import YFinanceProvider


def test_fake_provider_basics():
    """FakeProvider must return synthetic bars and P/E values."""
    provider = FakeProvider(print_banner=False)
    bars, count, fails = provider.download_bars(["TEST1.NS", "TEST2.NS"])

    assert len(bars) == 2
    assert "TEST1.NS" in bars
    assert "Close" in bars["TEST1.NS"].columns
    assert len(bars["TEST1.NS"]) == 100

    pe_dict, pe_count, pe_fails = provider.fetch_pe_batch(["TEST1.NS", "TEST7.NS"])
    assert "TEST1.NS" in pe_dict
    assert pe_dict["TEST1.NS"] == 14.5
    assert len(pe_fails) == 1
    assert pe_fails[0].ticker == "TEST7.NS"


def test_yfinance_provider_extract_ticker_df_shapes():
    """Verify YFinanceProvider._extract_ticker_df handles MultiIndex level 1, level 0, and flat shapes."""
    provider = YFinanceProvider(config=Settings(), clock=FakeClock())

    dates = pd.date_range("2026-09-01", periods=5, freq="B")

    # Shape 1: MultiIndex with (PriceField, Ticker) - level 1 ticker
    cols1 = pd.MultiIndex.from_tuples(
        [("Close", "AAA.NS"), ("Close", "BBB.NS"), ("Volume", "AAA.NS"), ("Volume", "BBB.NS")],
        names=["Price", "Ticker"],
    )
    df_multi1 = pd.DataFrame(
        [[100.0, 200.0, 1000.0, 2000.0]] * 5,
        index=dates,
        columns=cols1,
    )
    sub_aaa1 = provider._extract_ticker_df(df_multi1, "AAA.NS")
    assert sub_aaa1 is not None
    assert "Close" in sub_aaa1.columns
    assert "Volume" in sub_aaa1.columns
    assert sub_aaa1["Close"].iloc[0] == 100.0

    # Shape 2: MultiIndex with (Ticker, PriceField) - level 0 ticker
    cols2 = pd.MultiIndex.from_tuples(
        [("AAA.NS", "Close"), ("AAA.NS", "Volume"), ("BBB.NS", "Close"), ("BBB.NS", "Volume")],
        names=["Ticker", "Price"],
    )
    df_multi2 = pd.DataFrame(
        [[100.0, 1000.0, 200.0, 2000.0]] * 5,
        index=dates,
        columns=cols2,
    )
    sub_aaa2 = provider._extract_ticker_df(df_multi2, "AAA.NS")
    assert sub_aaa2 is not None
    assert "Close" in sub_aaa2.columns
    assert "Volume" in sub_aaa2.columns
    assert sub_aaa2["Close"].iloc[0] == 100.0

    # Shape 3: Flat columns (single ticker)
    df_flat = pd.DataFrame(
        {"Close": [150.0] * 5, "Volume": [5000.0] * 5},
        index=dates,
    )
    sub_flat = provider._extract_ticker_df(df_flat, "AAA.NS")
    assert sub_flat is not None
    assert "Close" in sub_flat.columns
    assert sub_flat["Close"].iloc[0] == 150.0

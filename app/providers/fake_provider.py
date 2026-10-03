"""Fake market data provider for tests and --offline CLI execution.

Uses ONLY synthetic fixtures. Prints SYNTHETIC DATA banner.
Never served by API or UI.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import pandas as pd
from loguru import logger

from app.core.interfaces import MarketDataProvider
from app.core.outcomes import FailedSymbolItem, FailureCode, Stage
from tests.fixtures.synthetic_data import (
    SYNTHETIC_BANNER,
    SYNTHETIC_PE,
    generate_synthetic_ohlcv,
)


class FakeProvider(MarketDataProvider):
    """Synthetic market data provider for offline testing."""

    def __init__(self, print_banner: bool = True) -> None:
        self.request_count = 0
        if print_banner:
            print(SYNTHETIC_BANNER)
            logger.info("FakeProvider initialized with SYNTHETIC DATA fixtures.")

    def download_bars(
        self, tickers: List[str]
    ) -> Tuple[Dict[str, pd.DataFrame], int, List[FailedSymbolItem]]:
        """Return synthetic OHLCV data for requested tickers."""
        data: Dict[str, pd.DataFrame] = {}
        failures: List[FailedSymbolItem] = []

        for ticker in tickers:
            self.request_count += 1
            df = generate_synthetic_ohlcv(ticker)
            data[ticker] = df

        return data, len(tickers), failures

    def fetch_pe_batch(
        self, tickers: List[str]
    ) -> Tuple[Dict[str, float], int, List[FailedSymbolItem]]:
        """Return synthetic P/E values for requested tickers."""
        pe_dict: Dict[str, float] = {}
        failures: List[FailedSymbolItem] = []

        for ticker in tickers:
            self.request_count += 1
            pe_val = SYNTHETIC_PE.get(ticker)
            if pe_val is None:
                failures.append(
                    FailedSymbolItem(
                        ticker=ticker,
                        stage=Stage.PE,
                        code=FailureCode.MISSING_PE,
                        message="Synthetic P/E is missing for ticker",
                    )
                )
            elif pe_val <= 0:
                failures.append(
                    FailedSymbolItem(
                        ticker=ticker,
                        stage=Stage.PE,
                        code=FailureCode.INVALID_PE,
                        message=f"Synthetic P/E is invalid: {pe_val}",
                    )
                )
            else:
                pe_dict[ticker] = pe_val

        return pe_dict, len(tickers), failures

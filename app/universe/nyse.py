"""NYSE Universe Loader from Nasdaq Trader 'otherlisted.txt' directory.

The source has no instrument-type column. Configurable name/symbol exclusions
remove preferreds, warrants, units, rights, debt, acquisition vehicles and funds.
Names do not need share-class wording: many operating-company descriptions omit it.
"Trust" alone is retained because genuine equity REITs use that corporate name.
Descriptions remain a heuristic with false positives and false negatives.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Dict, List, Optional, Union
from loguru import logger
import pandas as pd

from app.core.config import Settings
from app.core.interfaces import Universe, UniverseSymbol


class NYSEUniverse(Universe):
    """Loads and filters NYSE equity constituents from pipe-delimited otherlisted.txt."""

    REQUIRED_COLUMNS = ["ACT Symbol", "Security Name", "Exchange", "ETF", "Test Issue"]

    def __init__(
        self,
        file_path: Union[str, Path] = "data/otherlisted.txt",
        config: Optional[Settings] = None,
    ) -> None:
        self.file_path = Path(file_path)
        self.config = config or Settings()

    def load(self) -> List[UniverseSymbol]:
        """Load, parse, and filter NYSE common stock universe.

        Filters:
        - Drops trailing 'File Creation Time' row
        - Keeps Exchange == 'N' (NYSE)
        - Excludes ETF == 'Y'
        - Excludes Test Issue == 'Y'
        - Excludes symbols containing '$'
        - Excludes symbol suffixes in config.NYSE_EXCLUDE_SYMBOL_SUFFIXES
        - Excludes names matching regex in config.NYSE_EXCLUDE_NAME_PATTERN
        - Converts dot to hyphen for Yahoo (e.g. BRK.B -> BRK-B)
        """
        if not self.file_path.exists():
            raise FileNotFoundError(
                f"Required NYSE universe file not found: {self.file_path}. "
                f"Please place official otherlisted.txt at this location."
            )

        try:
            # Read pipe-delimited file
            df = pd.read_csv(self.file_path, sep="|", dtype=str, keep_default_na=False)
        except Exception as e:
            raise ValueError(f"Failed to read NYSE universe file {self.file_path}: {e}") from e

        # Normalize column headers
        df.columns = [str(c).strip() for c in df.columns]

        # Verify required columns exist
        missing_cols = [c for c in self.REQUIRED_COLUMNS if c not in df.columns]
        if missing_cols:
            raise ValueError(
                f"NYSE universe file {self.file_path} is missing required columns: {missing_cols}. "
                f"Found columns: {list(df.columns)}"
            )

        # 1. Drop trailing metadata rows (e.g. 'File Creation Time: ...')
        df = df[~df["ACT Symbol"].str.startswith("File Creation Time")].copy()
        df = df[df["ACT Symbol"].str.strip() != ""].copy()

        initial_count = len(df)

        # 2. Exchange == 'N' only
        non_nyse_count = int((df["Exchange"].str.strip() != "N").sum())
        df = df[df["Exchange"].str.strip() == "N"]

        # 3. Exclude ETFs and Test Issues
        etf_count = int((df["ETF"].str.strip().str.upper() == "Y").sum())
        df = df[df["ETF"].str.strip().str.upper() != "Y"]

        test_count = int((df["Test Issue"].str.strip().str.upper() == "Y").sum())
        df = df[df["Test Issue"].str.strip().str.upper() != "Y"]

        # 4. Pattern-based exclusions (preferred, warrants, units, rights)
        exclude_stats: Dict[str, int] = {
            "non_nyse_exchange": non_nyse_count,
            "etf": etf_count,
            "test_issue": test_count,
            "symbol_dollar_sign": 0,
            "symbol_suffix": 0,
            "name_pattern": 0,
        }

        # Compile regex from config
        name_regex = re.compile(self.config.NYSE_EXCLUDE_NAME_PATTERN, re.IGNORECASE)
        symbol_suffixes = tuple(s.upper() for s in self.config.NYSE_EXCLUDE_SYMBOL_SUFFIXES)

        symbols: List[UniverseSymbol] = []
        seen = set()

        for _, row in df.iterrows():
            act_sym = str(row["ACT Symbol"]).strip()
            name = str(row["Security Name"]).strip()

            if not act_sym or act_sym in seen:
                continue

            # Heuristic 4a: Symbol contains '$'
            if "$" in act_sym:
                exclude_stats["symbol_dollar_sign"] += 1
                continue

            # Heuristic 4b: Symbol ends with specific suffix (.WS, .U, etc.)
            act_sym_upper = act_sym.upper()
            if any(act_sym_upper.endswith(suf) for suf in symbol_suffixes):
                exclude_stats["symbol_suffix"] += 1
                continue

            # Heuristic 4c: Name matches preferred/warrant/unit/right regex
            if name_regex.search(name):
                exclude_stats["name_pattern"] += 1
                continue

            seen.add(act_sym)

            # Yahoo Ticker format: convert dots to hyphens (e.g. BRK.B -> BRK-B)
            yahoo_ticker = act_sym.replace(".", "-")

            symbols.append(
                UniverseSymbol(
                    symbol=act_sym,
                    company_name=name,
                    ticker=yahoo_ticker,
                    market="NYSE",
                )
            )

        total_excluded = initial_count - len(symbols)
        logger.info(
            f"Loaded {len(symbols)} NYSE constituents from {self.file_path} "
            f"(Total excluded: {total_excluded}). Excluded breakdown: {exclude_stats}"
        )

        return symbols

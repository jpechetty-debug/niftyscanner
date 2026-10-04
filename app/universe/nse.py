"""NSE Nifty 500 Universe Loader."""

from __future__ import annotations

from pathlib import Path
from typing import List, Union
import pandas as pd
from loguru import logger

from app.core.interfaces import Universe, UniverseSymbol

# Series codes that cannot pass screening (REIT/InvIT units lack trailing P/E).
_EXCLUDED_SERIES = {"RR"}


class NSEUniverse(Universe):
    """Loads NSE constituents from CSV file (e.g. data/nifty500.csv)."""

    def __init__(self, file_path: Union[str, Path] = "data/nifty500.csv") -> None:
        self.file_path = Path(file_path)

    def load(self) -> List[UniverseSymbol]:
        """Load and return constituent symbols from file.

        Required columns: 'Symbol', 'Company Name'.
        Optional column: 'Series' (used to exclude REIT/InvIT units).
        Yahoo ticker = Symbol + '.NS'.

        Exclusions applied during loading:
        - Symbols starting with 'DUMMY' (placeholder rows in official CSV).
        - Rows where Series is in _EXCLUDED_SERIES (e.g. 'RR' for REIT units).
        """
        if not self.file_path.exists():
            raise FileNotFoundError(
                f"Required universe file not found: {self.file_path}. "
                f"Please provide official NSE constituents CSV at this location."
            )

        try:
            df = pd.read_csv(self.file_path, comment="#")
        except Exception as e:
            raise ValueError(f"Failed to parse CSV file at {self.file_path}: {e}") from e

        # Normalize column names (strip whitespace)
        df.columns = [str(c).strip() for c in df.columns]

        # Case-insensitive column search if exact names differ in casing
        col_map = {c.lower(): c for c in df.columns}
        if "symbol" not in col_map or "company name" not in col_map:
            raise ValueError(
                f"Universe file {self.file_path} must contain 'Symbol' and 'Company Name' columns. "
                f"Found columns: {list(df.columns)}"
            )

        symbol_col = col_map["symbol"]
        name_col = col_map["company name"]
        series_col = col_map.get("series")

        # Filter valid rows
        valid_df = df.dropna(subset=[symbol_col, name_col])
        symbols: List[UniverseSymbol] = []
        seen = set()
        skipped_dummy = 0
        skipped_series = 0

        for _, row in valid_df.iterrows():
            sym = str(row[symbol_col]).strip()
            name = str(row[name_col]).strip()
            if not sym or sym in seen:
                continue

            # Drop placeholder rows (e.g. DUMMYHEG in official Nifty 500 CSV)
            if sym.upper().startswith("DUMMY"):
                skipped_dummy += 1
                continue

            # Drop REIT/InvIT units (series RR) that can never pass P/E screening
            if series_col is not None:
                series_val = str(row[series_col]).strip().upper()
                if series_val in _EXCLUDED_SERIES:
                    skipped_series += 1
                    continue

            seen.add(sym)
            ticker = f"{sym}.NS"
            symbols.append(
                UniverseSymbol(
                    symbol=sym,
                    company_name=name,
                    ticker=ticker,
                    market="NSE",
                )
            )

        if skipped_dummy or skipped_series:
            logger.info(
                f"NSE universe exclusions: {skipped_dummy} DUMMY placeholder(s), "
                f"{skipped_series} series-RR REIT/InvIT unit(s)"
            )
        logger.info(f"Loaded {len(symbols)} symbols from NSE universe {self.file_path}")
        return symbols

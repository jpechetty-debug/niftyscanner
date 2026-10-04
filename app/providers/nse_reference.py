"""Optional dated reference data. Missing context never changes the screening funnel.

CSV contract: Symbol,Date,Source,PE,PriceBandPct,FNO. PE, band and FNO are optional.
Users supply independently checked data; this module does not invent NSE fundamentals.
"""
import csv
import math
from pathlib import Path
from typing import Protocol

from loguru import logger
from app.core.config import Settings
from app.core.interfaces import ScanResultItem


class ReferenceProvider(Protocol):
    def load(self) -> dict[tuple[str, str], dict]: ...


class CSVReferenceProvider:
    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> dict[tuple[str, str], dict]:
        if not self.path:
            return {}
        try:
            with Path(self.path).open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            result = {}
            for row in rows:
                symbol = row["Symbol"].strip().upper().removesuffix(".NS")
                key = (symbol, row["Date"].strip())
                if key in result:
                    # Ambiguous duplicate reference records must not validate a signal.
                    result[key] = None
                else:
                    result[key] = row
            return {key: row for key, row in result.items() if row is not None}
        except (OSError, KeyError, AttributeError, TypeError, csv.Error, UnicodeError) as error:
            logger.warning("NSE reference file unavailable: {}", error)
            return {}


def positive_number(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError):
        return None


def enrich_result(item: ScanResultItem, config: Settings, references: dict[tuple[str, str], dict]) -> None:
    """Band proximity is a caution, never evidence that the next open is executable."""
    if item.market != "NSE":
        item.circuit_risk_status = "not_applicable"
        item.circuit_risk_reason = "NSE circuit-band check does not apply to this market"
        return
    row = references.get((item.ticker.upper().removesuffix(".NS"), item.bar_date))
    band = None
    if row and row.get("Source", "").strip():
        item.reference_source = row["Source"].strip()
        item.reference_date = item.bar_date
        item.pe_reference = positive_number(row.get("PE"))
        if item.pe_reference is not None:
            difference = 100 * abs(item.pe / item.pe_reference - 1)
            item.pe_check_status = "match" if difference <= config.PE_REFERENCE_TOLERANCE_PCT else "divergent"
        # Derivatives-eligible equities have flexible operating ranges, not fixed bands.
        fno = row.get("FNO", "").strip().lower()
        if fno in {"true", "1", "yes"}:
            item.circuit_risk_status = "dynamic_band"
            item.circuit_risk_reason = "F&O operating ranges can flex; fixed-band check unavailable"
            return
        # Require explicit non-F&O classification before using a supplied fixed band.
        if fno in {"false", "0", "no"}:
            band = positive_number(row.get("PriceBandPct"))
            if band is not None and band > 100:
                band = None
    move = item.day_change_pct
    if move is None or not math.isfinite(move):
        return
    if band is not None:
        item.circuit_risk = abs(move) >= band - config.CIRCUIT_PROXIMITY_PCT
        item.circuit_risk_status = "reference_band"
        item.circuit_risk_reason = f"Daily move versus supplied {band:g}% band; adjusted-close proxy"
    else:
        candidates = [float(value) for value in config.CIRCUIT_BANDS_PCT.split(",")]
        if any(abs(abs(move) - value) <= config.CIRCUIT_PROXIMITY_PCT for value in candidates):
            item.circuit_risk = True
            item.circuit_risk_status = "indicative"
            item.circuit_risk_reason = "Move near a common NSE band; actual band and F&O status unverified"

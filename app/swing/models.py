"""Explicit vintage, adjustment and availability contracts for swing observations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PriceBasis = Literal["unknown", "provider_adjusted_unknown", "raw", "split_adjusted", "cash_dividend_adjusted", "total_return"]
ContextKind = Literal["delivery", "sector", "earnings", "surveillance", "fundamentals"]


def utc(value: datetime) -> datetime:
    """Reject ambiguous instants instead of interpreting a naive local clock."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware instant is required")
    return value.astimezone(timezone.utc)


class DailyPrice(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")
    ticker: str = Field(min_length=1)
    session: date
    instrument: Literal["equity", "index"] = "equity"
    open: float | None = Field(default=None, gt=0)
    high: float | None = Field(default=None, gt=0)
    low: float | None = Field(default=None, gt=0)
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)
    price_basis: PriceBasis = "unknown"
    currency: Literal["INR"] = "INR"
    series: str | None = None
    isin: str | None = None

    @model_validator(mode="after")
    def valid_bar(self):
        if self.instrument == "equity" and (any(v is None for v in (self.open, self.high, self.low, self.volume)) or self.volume <= 0):
            raise ValueError("Equity needs full OHLC and positive observed volume")
        if all(v is not None for v in (self.open, self.high, self.low)):
            if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
                raise ValueError("Inconsistent OHLC")
        if (self.high is not None and self.close > self.high) or (self.low is not None and self.close < self.low):
            raise ValueError("Close outside supplied high/low")
        return self


class DatedContext(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")
    kind: ContextKind
    ticker: str = Field(min_length=1)
    session: date
    source: str = Field(min_length=1)
    ingested_at: datetime
    published_at: datetime | None = None
    status: Literal["available", "unavailable", "stale", "conflicting", "not_applicable"]
    values: dict[str, Any] = Field(default_factory=dict)
    publication_verified: bool = False
    publication_evidence: str | None = None

    @field_validator("ingested_at", "published_at")
    @classmethod
    def aware_instants(cls, value):
        return utc(value) if value is not None else None

    @model_validator(mode="after")
    def valid_availability(self):
        json.dumps(self.values, allow_nan=False)
        if not self.source.strip() or (self.status == "available" and not self.values):
            raise ValueError("Available context needs values and a nonempty source")
        if self.published_at is not None and self.published_at > self.ingested_at:
            raise ValueError("Cannot claim ingestion before publication")
        if self.publication_verified and (self.published_at is None or not self.publication_evidence):
            raise ValueError("Verified publication needs a timestamp and evidence")
        if self.session > self.ingested_at.astimezone(ZoneInfo("Asia/Kolkata")).date():
            raise ValueError("Context session lies after ingestion")
        return self

    @property
    def available_at(self) -> datetime:
        return max(self.ingested_at, self.published_at or self.ingested_at)


@dataclass(frozen=True)
class HistoryRequest:
    ticker: str
    start: date
    end: date
    as_of: datetime
    vintage: str
    required_basis: PriceBasis

    def __post_init__(self):
        utc(self.as_of)
        if self.start > self.end or not self.vintage or self.required_basis not in {"raw", "split_adjusted", "cash_dividend_adjusted", "total_return"}:
            raise ValueError("History requires an explicit vintage, known basis and valid date range")
        if self.end > self.as_of.astimezone(ZoneInfo("Asia/Kolkata")).date():
            raise ValueError("History end is after the decision date")


@dataclass(frozen=True)
class HistoryResult:
    status: Literal["available", "unavailable", "conflicting"]
    reason: str | None
    rows: tuple[DailyPrice, ...] = ()


@dataclass(frozen=True)
class ContextRequest:
    ticker: str
    kind: ContextKind
    session: date
    as_of: datetime
    vintage: str

    def __post_init__(self):
        utc(self.as_of)
        if not self.vintage or self.kind not in {"delivery", "sector", "earnings", "surveillance", "fundamentals"}:
            raise ValueError("Explicit vintage and supported context kind required")
        if self.session > self.as_of.astimezone(ZoneInfo("Asia/Kolkata")).date():
            raise ValueError("Context session is after the decision date")


class DailyHistoryProvider(Protocol):
    def history(self, request: HistoryRequest) -> HistoryResult: ...


class ContextProvider(Protocol):
    def context(self, request: ContextRequest) -> DatedContext: ...

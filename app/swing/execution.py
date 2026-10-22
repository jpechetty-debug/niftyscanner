"""Explicit execution evidence protocol. S1 cache does not attest tradable fills."""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

import pandas as pd

from app.swing.models import DailyPrice, HistoryRequest, utc
from app.swing.risk import ReturnPoint


@dataclass(frozen=True)
class ExecutionObservation:
    bar: DailyPrice | None
    available_at: datetime
    evidence: str
    reason: str | None = None
    actions_verified: bool = False
    tradable: bool | None = None
    buy_capacity: int | None = None
    sell_capacity: int | None = None
    action_id: str | None = None
    split_ratio: str | None = None
    previous_isin: str | None = None
    unsupported_action: bool = False

    def __post_init__(self):
        utc(self.available_at)
        if not self.evidence:
            raise ValueError("Execution evidence required")
        for value in (self.buy_capacity, self.sell_capacity):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Nonnegative integer capacity required; daily volume is not a substitute")
        if self.split_ratio is not None and not self.action_id:
            raise ValueError("Corporate-action ID required")


class ReplayMarket(Protocol):
    def observe(self, ticker: str, session: date, vintage: str, as_of: datetime) -> ExecutionObservation: ...
    def returns(self, ticker: str, session: date, count: int, vintage: str, as_of: datetime) -> tuple[ReturnPoint, ...]: ...


class ArchiveReplayMarket:
    """Read only S1; never infer capacity, corporate actions or tradability."""
    def __init__(self, repository, calendar):
        self.repository, self.calendar = repository, calendar

    def observe(self, ticker, session, vintage, as_of):
        result = self.repository.history(HistoryRequest(ticker, session, session, as_of, vintage, "raw"))
        return ExecutionObservation(result.rows[0] if result.status == "available" else None,
                                    utc(as_of), vintage, result.reason or "execution_context_unverified")

    def returns(self, ticker, session, count, vintage, as_of):
        sessions = self.calendar.calendar.sessions
        window = sessions[sessions <= pd.Timestamp(session)][-count-1:]
        if len(window) != count+1:
            return ()
        data = self.repository.history(HistoryRequest(ticker, window[0].date(), session, as_of, vintage, "raw"))
        if data.status != "available":
            return ()
        return tuple(ReturnPoint(r.session, r.close / prior.close - 1) for prior, r in zip(data.rows, data.rows[1:]))

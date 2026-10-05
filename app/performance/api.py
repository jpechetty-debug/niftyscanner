"""HTTP-only performance reporting, with validated filters and worker-thread IO."""
from datetime import date
from typing import Any, Literal
import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api")


class PerformanceResponse(BaseModel):
    supported: bool
    message: str | None = None
    market: str | None = None
    horizon: int | None = None
    benchmark: str | None = None
    price_basis: str | None = None
    return_basis: str = "gross"
    cost_model: dict[str, Any] = Field(default_factory=dict)
    enabled: bool | None = None
    scheduler_running: bool = False
    nightly_time: str | None = None
    strategies: list[str] = Field(default_factory=list)
    duplicates_removed: int = 0
    summary: dict[str, Any] = Field(default_factory=dict)
    groups: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    outcomes: list[dict[str, Any]] = Field(default_factory=list)
    job: dict[str, Any] | None = None


@router.get("/performance", response_model=PerformanceResponse)
async def performance(request: Request, market: str = "NSE",
                      horizon: int = 5,
                      start: date | None = None, end: date | None = None,
                      strategy: str | None = Query(default=None, max_length=100),
                      return_basis: Literal["gross", "net"] = "gross"):
    from app.api.routes import validate_market_parameter
    market = validate_market_parameter(market, request.app.state.config.enabled_markets_list)
    if horizon not in (1, 5, 10):
        raise HTTPException(422, "Horizon must be 1, 5 or 10 sessions")
    if start and end and start > end:
        raise HTTPException(422, "Start date must not follow end date")
    service = request.app.state.performance
    if market != "NSE" or service is None:
        return {"supported": False, "message": "Performance tracking currently supports NSE against Nifty 500."}
    report = await asyncio.to_thread(service.report, market, horizon,
        start.isoformat() if start else None, end.isoformat() if end else None, strategy, return_basis)
    report["scheduler_running"] = request.app.state.scheduler.is_running
    return report


@router.get("/performance/score-buckets")
async def score_buckets(request: Request, market: str = "NSE", horizon: int = 5):
    """Historical resolved outcomes per score band, for per-row context in the results view."""
    from app.api.routes import validate_market_parameter
    market = validate_market_parameter(market, request.app.state.config.enabled_markets_list)
    if horizon not in (1, 5, 10):
        raise HTTPException(422, "Horizon must be 1, 5 or 10 sessions")
    service = request.app.state.performance
    if market != "NSE" or service is None:
        return {"supported": False, "horizon": horizon, "buckets": []}
    return await asyncio.to_thread(service.score_track_record, horizon)

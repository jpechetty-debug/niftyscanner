"""Opt-in, HTTP-only research reads; blocking SQLite/hash/calculation stays off-loop."""
import asyncio
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from app.swing.plans import Plan, Rejection
from app.swing.repository import SwingRepository
from app.swing.service import SwingResearchService, UnknownVintage

router = APIRouter(prefix="/api/swing", tags=["swing research"])
Vintage = Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")]


class ResearchResponse(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())
    market: Literal["NSE"]
    vintage: str
    model_status: Literal["RESEARCH_ONLY"]
    paper_eligible: Literal[False]
    plans: list[Plan]
    failed: list[Rejection]
    filtered: list[Rejection]
    funnel: dict[str, int]
    missing_gates: list[str]


class StatusResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    enabled: bool
    mode: Literal["research"]
    model_status: Literal["RESEARCH_ONLY"]
    strategies: list[str]
    capital_configured: bool
    scheduled: Literal[False]
    broker_execution: Literal[False]
    replay_enabled: bool = False
    replay_worker_running: bool = False


class DataQualityResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    snapshot_id: str
    summary: dict[str, int]
    benchmark: dict
    members: list[dict]
    limitations: list[str]


def service(request: Request, market: str) -> SwingResearchService:
    cfg = request.app.state.config
    if market not in {"NSE", "NYSE"}:
        raise HTTPException(422, "Unknown market")
    if market != "NSE" or market not in cfg.enabled_markets_list or not cfg.SWING_ENABLED:
        raise HTTPException(404, "Swing research is disabled or unsupported for this market")
    calendar = request.app.state.calendars["NSE"]
    return SwingResearchService(SwingRepository(Path(cfg.SWING_DB_PATH), cfg.SQLITE_BUSY_TIMEOUT_SEC, calendar),
                                cfg, request.app.state.clock, calendar)


async def read(operation, vintage):
    try:
        return await asyncio.to_thread(operation, vintage)
    except UnknownVintage as exc:
        raise HTTPException(404, str(exc)) from exc
    except (FileNotFoundError, sqlite3.OperationalError) as exc:
        raise HTTPException(503, "Swing store unavailable; import and verify a real archive first") from exc
    except (ValueError, KeyError, sqlite3.DatabaseError, OSError) as exc:
        raise HTTPException(409, "Swing evidence or store failed verification") from exc


@router.get("/status", response_model=StatusResponse)
async def status(request: Request):
    cfg = request.app.state.config
    return {"enabled": cfg.SWING_ENABLED and "NSE" in cfg.enabled_markets_list, "mode": cfg.SWING_MODE,
            "model_status": "RESEARCH_ONLY", "strategies": ["DAILY_BREAKOUT_V1", "DAILY_PULLBACK_V1"],
            "capital_configured": cfg.SWING_CAPITAL_INR is not None, "scheduled": False, "broker_execution": False,
            "replay_enabled": cfg.SWING_ENABLED and cfg.SWING_REPLAY_ENABLED and "NSE" in cfg.enabled_markets_list,
            "replay_worker_running": cfg.SWING_ENABLED and cfg.SWING_REPLAY_ENABLED and "NSE" in cfg.enabled_markets_list
                                     and request.app.state.scheduler.is_running}


@router.get("/results", response_model=ResearchResponse)
async def results(request: Request, vintage: Vintage, market: str = "NSE"):
    return await read(service(request, market).results, vintage)


@router.get("/data-quality", response_model=DataQualityResponse)
async def data_quality(request: Request, vintage: Vintage, market: str = "NSE"):
    return await read(service(request, market).data_quality, vintage)

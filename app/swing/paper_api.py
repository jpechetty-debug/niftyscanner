"""Local opt-in research commands; no raw market/fill/promotion injection."""
import asyncio
from dataclasses import asdict
from datetime import date
from decimal import Decimal
import json
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.swing.ledger import PaperConflict, PaperNotFound, encoded
from app.swing.service import UnknownVintage
from app.swing.plans import Plan

router = APIRouter(prefix="/api/swing", tags=["research replay"])


class AccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    capital_inr: Decimal = Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    session: date
    key: str = Field(min_length=1, max_length=120)


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    vintage: str = Field(pattern=r"^[a-f0-9]{64}$")
    through: date
    expected_version: int = Field(ge=1)
    key: str = Field(min_length=1, max_length=120)
    management_only: bool = False


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    key: str = Field(min_length=1, max_length=120)
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class PaperView(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())
    account_id: str
    mode: Literal["RESEARCH_REPLAY"]


class JobView(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    status: Literal["QUEUED", "DONE", "FAILED"]


class EventsView(BaseModel):
    events: list[dict]
    next_sequence: int


class PerformanceView(PaperView):
    model_status: Literal["RESEARCH_ONLY"]
    resolved_trades: int
    unresolved_trades: int
    resolved_net_pnl: str
    equity: list[dict]
    ambiguity_sensitivity: dict


def paper(request, market="NSE", *, mutate=False):
    if market not in {"NSE", "NYSE"}:
        raise HTTPException(422, "Unknown market")
    if market != "NSE" or request.app.state.paper is None:
        raise HTTPException(404, "Research replay supports NSE only")
    cfg = request.app.state.config
    if mutate and (not cfg.SWING_ENABLED or not cfg.SWING_REPLAY_ENABLED):
        raise HTTPException(404, "Research replay mutations are disabled")
    return request.app.state.paper


async def invoke(operation, *args):
    try:
        result = await asyncio.to_thread(operation, *args)
        return json.loads(encoded(result))
    except (PaperNotFound, UnknownVintage) as exc:
        raise HTTPException(404, str(exc)) from exc
    except PaperConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (FileNotFoundError, sqlite3.OperationalError) as exc:
        raise HTTPException(503, "Research store unavailable") from exc
    except (ValueError, sqlite3.DatabaseError, OSError) as exc:
        raise HTTPException(409, "Research command or evidence failed validation") from exc


@router.post("/paper/accounts", response_model=PaperView)
async def create_account(request: Request, body: AccountRequest, market: str = "NSE"):
    svc = paper(request, market, mutate=True)
    return await invoke(svc.create_account, body.account_id, body.capital_inr, body.session, body.key)


@router.post("/replay", status_code=202, response_model=JobView)
async def enqueue(request: Request, body: ReplayRequest, market: str = "NSE"):
    svc = paper(request, market, mutate=True)
    job_id = await invoke(svc.enqueue, body.account_id, body.key, body.vintage, body.through, body.expected_version, body.management_only)
    request.app.state.scheduler.wake()
    return await invoke(svc.ledger.job, job_id)


@router.get("/paper/accounts/{account_id}", response_model=PaperView)
async def account(request: Request, account_id: str, market: str = "NSE"):
    return await invoke(paper(request, market).ledger.state, account_id)


@router.post("/paper/orders/{plan_id}/cancel", response_model=PaperView)
async def cancel(request: Request, plan_id: str, body: CancelRequest, market: str = "NSE"):
    return await invoke(paper(request, market, mutate=True).cancel, body.account_id, plan_id,
                        body.key, body.expected_version, body.reason)


@router.get("/plans/{plan_id}", response_model=Plan)
async def plan(request: Request, plan_id: str, account_id: str, market: str = "NSE"):
    svc = paper(request, market)
    def load():
        return asdict(svc.ledger.plan(account_id, plan_id))
    return await invoke(load)


@router.get("/paper/events", response_model=EventsView)
async def events(request: Request, account_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000), market: str = "NSE"):
    result = await invoke(paper(request, market).ledger.events, account_id, after, limit)
    return {"events": result, "next_sequence": result[-1]["seq"] if result else after}


@router.get("/paper/jobs/{job_id}", response_model=JobView)
async def job(request: Request, job_id: str, market: str = "NSE"):
    return await invoke(paper(request, market).ledger.job, job_id)


@router.get("/paper/performance", response_model=PerformanceView)
async def performance(request: Request, account_id: str, market: str = "NSE"):
    return await invoke(paper(request, market).performance, account_id)

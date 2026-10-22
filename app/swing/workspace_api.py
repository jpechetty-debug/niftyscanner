"""S5 HTTP presentation/control endpoints. No raw fill input or holdout opening."""
import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.swing.paper_api import invoke

router = APIRouter(prefix="/api/swing", tags=["trade ideas workspace"])


class WorkspaceView(BaseModel):
    research: dict
    export_csv: str
    source: dict
    controls: dict
    scope: str


class VintageView(BaseModel):
    vintage: str
    imported_at: str
    source: str
    verified_on_selection: bool


class OperationsView(BaseModel):
    controls: dict
    history: list[dict]
    scheduler_running: bool
    swing_enabled: bool
    replay_enabled: bool
    replay_worker_running: bool
    automatic_ideas_scheduled: Literal[False] = False
    outcomes_enabled: bool
    outcomes_worker_running: bool
    outcomes_time_ist: str
    webhook_configured: bool
    outbox: list[dict]
    last_completed_session: str
    read_only_preview: bool = False


class ChartView(BaseModel):
    vintage: str
    ticker: str
    price_basis: str
    observations: list[dict]
    missing_sessions: list[str]
    trade_usable: Literal[False]
    notice: str


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    key: str = Field(min_length=1, max_length=120)
    paused: bool | None = None
    alerts_paused: bool | None = None
    selected_vintage: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    rollback_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def meaningful(self):
        changes = self.model_dump(exclude_unset=True, exclude={"key", "expected_revision"})
        if not changes or any(changes.get(k, True) is None for k in ("paused", "alerts_paused")):
            raise ValueError("Explicit control changes required")
        if "rollback_revision" in changes and (changes["rollback_revision"] is None or len(changes) != 1):
            raise ValueError("Rollback is an exclusive explicit revision")
        return self


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=120)


def workspace(request, market="NSE"):
    if market not in {"NSE", "NYSE"}:
        raise HTTPException(422, "Unknown market")
    if market != "NSE" or request.app.state.swing_workspace is None:
        raise HTTPException(404, "Trade Ideas supports enabled NSE research only")
    return request.app.state.swing_workspace


async def present(operation, *args):
    # jsonable_encoder preserves frozen dataclasses/aware timestamps as JSON objects.
    return jsonable_encoder(await invoke_async(operation, *args))


async def invoke_async(operation, *args):
    from app.swing.ledger import PaperConflict, PaperNotFound
    from app.swing.service import UnknownVintage
    import sqlite3
    try:
        return await asyncio.to_thread(operation, *args)
    except (UnknownVintage, PaperNotFound) as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(503, "Research store unavailable; import real evidence first") from exc
    except (ValueError, KeyError, sqlite3.DatabaseError, OSError) as exc:
        raise HTTPException(409, "Research evidence/control failed validation; reload and review") from exc


@router.get("/vintages", response_model=list[VintageView])
async def vintages(request: Request, market: str = "NSE"):
    return await present(workspace(request, market).vintages)


@router.get("/workspace", response_model=WorkspaceView)
async def ideas(request: Request, vintage: str = Query(pattern=r"^[a-f0-9]{64}$"),
                strategy: Literal["DAILY_BREAKOUT_V1", "DAILY_PULLBACK_V1"] | None = None,
                sector: str | None = Query(None, max_length=120), search: str = Query("", max_length=120), market: str = "NSE"):
    return await present(workspace(request, market).view, vintage, strategy, sector, search)


@router.get("/export.csv")
async def export(request: Request, vintage: str = Query(pattern=r"^[a-f0-9]{64}$"),
                 strategy: Literal["DAILY_BREAKOUT_V1", "DAILY_PULLBACK_V1"] | None = None,
                 sector: str | None = Query(None, max_length=120), search: str = Query("", max_length=120), market: str = "NSE"):
    from app.swing.workspace import plans_csv
    service = workspace(request, market)
    def build():
        view = service.ideas(vintage, strategy, sector, search)
        return plans_csv(view["plans"], view.get("names"))
    text = await present(build)
    return Response(text, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="swing-research.csv"'})


@router.get("/chart", response_model=ChartView)
async def chart(request: Request, vintage: str = Query(pattern=r"^[a-f0-9]{64}$"),
                ticker: str = Query(pattern=r"^[A-Za-z0-9&^._-]{1,64}$"), bars: int | None = Query(None, ge=20, le=500), market: str = "NSE"):
    service = workspace(request, market)
    maximum = service.operations.settings.SWING_CHART_BARS
    if bars is not None and bars > maximum:
        raise HTTPException(422, "Chart exceeds the configured observation range")
    return await present(service.chart, vintage, ticker, bars or maximum)


@router.get("/paper/accounts", response_model=list[str])
async def accounts(request: Request, market: str = "NSE"):
    return await present(workspace(request, market).accounts)


@router.get("/allocation", response_model=dict)
async def allocation(request: Request, account_id: str, vintage: str = Query(pattern=r"^[a-f0-9]{64}$"),
                     strategy: Literal["DAILY_BREAKOUT_V1", "DAILY_PULLBACK_V1"] | None = None,
                     sector: str | None = Query(None, max_length=120), search: str = Query("", max_length=120), market: str = "NSE"):
    return await present(workspace(request, market).allocation, vintage, account_id, strategy, sector, search)


@router.get("/validation/reports", response_model=list[dict])
async def reports(request: Request, market: str = "NSE"):
    return await present(workspace(request, market).validation_reports)


@router.get("/validation/reports/{report_id}", response_model=dict)
async def report(request: Request, report_id: str, market: str = "NSE"):
    result = await present(workspace(request, market).validation_report, report_id)
    if result is None:
        raise HTTPException(404, "Report unavailable")
    return result


@router.get("/operations", response_model=OperationsView)
async def operations(request: Request, market: str = "NSE"):
    service = workspace(request, market)
    cfg, scheduler = request.app.state.config, request.app.state.scheduler
    from app.providers.swing_archive import last_completed
    def describe():
        running = scheduler.is_running
        return {"controls": service.operations.controls(), "history": service.operations.history(),
            "scheduler_running": running, "swing_enabled": cfg.SWING_ENABLED,
            "replay_enabled": cfg.SWING_ENABLED and cfg.SWING_REPLAY_ENABLED,
            "replay_worker_running": running and cfg.SWING_ENABLED and cfg.SWING_REPLAY_ENABLED,
            "outcomes_enabled": cfg.PERFORMANCE_ENABLED and request.app.state.performance is not None,
            "outcomes_worker_running": running and cfg.PERFORMANCE_ENABLED and request.app.state.performance is not None,
            "outcomes_time_ist": cfg.PERFORMANCE_NIGHTLY_TIME, "webhook_configured": service.operations.webhook_configured,
            "read_only_preview": getattr(request.app.state, "read_only_preview", False),
            "outbox": service.operations.outbox(), "last_completed_session": last_completed(service.calendar, service.clock.now())}
    return await present(describe)


async def control_lock(request, operation, *args):
    scheduler = request.app.state.scheduler
    retry = max(1, request.app.state.config.REFRESH_COOLDOWN_SEC)
    if scheduler.state.scan_lock.locked() or scheduler._reserved_market or scheduler._stopping:
        raise HTTPException(429, {"message": "Shared worker busy; control not applied", "retry_after": retry}, headers={"Retry-After": str(retry)})
    async with scheduler.state.scan_lock:
        return await present(operation, *args)


@router.post("/operations", response_model=dict)
async def change(request: Request, body: ControlRequest, market: str = "NSE"):
    service = workspace(request, market)
    changes = body.model_dump(exclude_unset=True, exclude={"expected_revision", "key"})
    def validate(material):
        if material.get("selected_vintage"):
            service.snapshot(material["selected_vintage"])
    result = await control_lock(request, service.operations.change, body.key, body.expected_revision, changes, validate)
    request.app.state.scheduler.wake()
    return result


@router.post("/operations/alerts/{event_id}/retry", response_model=dict)
async def retry(request: Request, event_id: str, body: RetryRequest, market: str = "NSE"):
    result = await control_lock(request, workspace(request, market).operations.retry, event_id, body.key)
    request.app.state.scheduler.wake()
    return result

"""FastAPI route handlers for stock screener REST API."""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.api.models import (
    RefreshAcceptedResponse,
    ResultsResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    StatusResponse,
)
from app.cache.persistence import save_settings_file

router = APIRouter(prefix="/api")

KNOWN_MARKETS = {"NSE", "NYSE"}


def validate_market_parameter(market: str, enabled_markets: list[str]) -> str:
    """Validate market name according to Section 17 rules.

    - Unknown market -> 422 Unprocessable Entity
    - Known but not enabled -> 404 Not Found
    """
    market_upper = market.strip().upper()
    if market_upper not in KNOWN_MARKETS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown market '{market}'. Supported markets are: {list(KNOWN_MARKETS)}",
        )

    if market_upper not in enabled_markets:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Market '{market_upper}' is recognized but currently disabled in configuration.",
        )

    return market_upper


@router.get("/results", response_model=ResultsResponse)
async def get_results(
    request: Request,
    market: str = Query("NSE", description="Market code (default: NSE)"),
):
    """Retrieve the latest screening results and funnel metadata for a market."""
    state_manager = request.app.state.state_manager
    config = request.app.state.config
    market_valid = validate_market_parameter(market, config.enabled_markets_list)

    payload = state_manager.get_results_payload(market_valid)
    return payload


@router.get("/status", response_model=StatusResponse)
async def get_status(
    request: Request,
    market: str = Query("NSE", description="Market code (default: NSE)"),
):
    """Retrieve operational status, circuit breaker states, and staleness."""
    state_manager = request.app.state.state_manager
    config = request.app.state.config
    market_valid = validate_market_parameter(market, config.enabled_markets_list)
    return state_manager.get_status_payload(market_valid)


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED, response_model=RefreshAcceptedResponse)
async def trigger_refresh(
    request: Request,
    market: str = Query("NSE", description="Market code to refresh"),
):
    """Trigger an immediate screening scan, respecting single-flight lock and cooldown."""
    scheduler = request.app.state.scheduler
    config = request.app.state.config
    market_valid = validate_market_parameter(market, config.enabled_markets_list)

    allowed, retry_after = scheduler.trigger_immediate_scan(market_valid)
    if not allowed:
        retry_after = retry_after or config.REFRESH_COOLDOWN_SEC
        headers = {"Retry-After": str(retry_after)}
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"message": "Scan already active or cooldown period in effect.", "retry_after": retry_after},
            headers=headers,
        )

    return RefreshAcceptedResponse(
        message="Scan initiated",
        market=market_valid,
        timestamp=request.app.state.clock.now().isoformat(),
    )


@router.get("/export.csv")
async def export_csv(
    request: Request,
    market: str = Query("NSE", description="Market code to export"),
):
    """Export the latest qualified screening results as a CSV spreadsheet."""
    state_manager = request.app.state.state_manager
    config = request.app.state.config
    market_valid = validate_market_parameter(market, config.enabled_markets_list)

    payload = state_manager.get_results_payload(market_valid)
    results = payload.get("results", [])

    output = io.StringIO()
    fieldnames = [
        "ticker",
        "name",
        "market",
        "price",
        "pe",
        "rsi",
        "volume",
        "avg_volume_20d",
        "volume_ratio",
        "score",
        "session_partial",
        "bar_date",
        "day_change_pct", "circuit_risk", "circuit_risk_status", "circuit_risk_reason",
        "reference_source", "reference_date", "pe_reference", "pe_check_status",
        "is_new", "first_seen_at", "scans_qualified",
        "score_volume", "score_rsi", "score_pe",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for item in results:
        writer.writerow({k: item.get(k) for k in fieldnames})

    csv_data = output.getvalue()
    filename = f"screener_{market_valid}_{request.app.state.clock.now().strftime('%Y%m%d')}.csv"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}

    return Response(content=csv_data, media_type="text/csv", headers=headers)


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(request: Request):
    """Retrieve active runtime refresh settings."""
    config = request.app.state.config
    return SettingsResponse(refresh_interval_sec=config.REFRESH_INTERVAL_SEC)


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(update: SettingsUpdateRequest, request: Request):
    """Update runtime refresh interval immediately and persist atomically to disk."""
    config = request.app.state.config

    # Persist first so a failed write cannot silently change runtime settings.
    await asyncio.to_thread(save_settings_file, {"REFRESH_INTERVAL_SEC": update.refresh_interval_sec})
    config.REFRESH_INTERVAL_SEC = update.refresh_interval_sec
    request.app.state.scheduler.wake()

    return SettingsResponse(refresh_interval_sec=config.REFRESH_INTERVAL_SEC)

"""FastAPI router for Macro Market Indicators (India VIX, Brent, Nifty 50)."""

from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException, Request, status

from app.macro.models import MacroOverviewResponse

router = APIRouter(prefix="/api/macro", tags=["Macro Indicators"])


@router.get("", response_model=MacroOverviewResponse)
async def get_macro_indicators(request: Request):
    """Retrieve the latest macro market indicators (India VIX, Brent Crude, Nifty 50).

    The fetch is offloaded to a worker thread via asyncio.to_thread to maintain the
    invariant that the FastAPI event loop remains non-blocking.
    """
    provider = getattr(request.app.state, "macro_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Macro indicator service is not initialized or disabled in configuration.",
        )

    try:
        data = await asyncio.to_thread(provider.get_macro_data)
        return data
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to retrieve macro market data: {e}",
        )

"""Read-only S6 monitoring. No external observations or proof claims accepted."""
from fastapi import APIRouter, HTTPException, Request

from app.swing.workspace_api import present

router = APIRouter(prefix="/api/swing/forward", tags=["forward paper validation"])


def forward(request, market):
    if market not in {"NSE", "NYSE"}:
        raise HTTPException(422, "Unknown market")
    service = request.app.state.forward
    if market != "NSE" or service is None:
        raise HTTPException(404, "Forward observation supports NSE only")
    return service


@router.get("/campaigns", response_model=list[dict])
async def campaigns(request: Request, market: str = "NSE"):
    return await present(forward(request, market).campaigns)


@router.get("/campaigns/{campaign}", response_model=dict)
async def campaign_report(request: Request, campaign: str, market: str = "NSE"):
    def read():
        service = forward(request, market)
        # Same verified live-only discovery gate applies to direct IDs.
        return next((r for r in service.campaigns() if r["campaign"] == campaign), None)
    result = await present(read)
    if result is None:
        raise HTTPException(404, "Forward campaign unavailable")
    return result

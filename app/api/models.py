"""API response models matching Section 17 specifications."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.interfaces import ScanResultItem
from app.core.outcomes import FailedSymbolItem, FunnelCounts
from app.services.changes import DroppedItem


class MetaInfo(BaseModel):
    """Metadata describing scan execution and freshness."""
    market: str
    market_status: str
    stale: bool
    stale_reasons: List[str] = Field(default_factory=list)
    data_as_of: str
    last_refreshed: str
    next_refresh_at: Optional[str] = None
    effective_interval_sec: int
    scan_seconds: float
    request_count: int
    funnel: FunnelCounts
    new_entries: List[str] = Field(default_factory=list)
    dropped: List[DroppedItem] = Field(default_factory=list)
    changes_compared_to: Optional[str] = None


class ResultsResponse(BaseModel):
    """Full screening response returned by GET /api/results."""
    meta: MetaInfo
    results: List[ScanResultItem]
    failed_symbols: List[FailedSymbolItem]


class StatusResponse(BaseModel):
    """System health and scan status returned by GET /api/status."""
    market: str
    is_scanning: bool
    market_status: Dict[str, Any]
    download_breaker: str
    pe_breaker: str
    stale: bool
    stale_reasons: List[str]
    effective_interval_sec: int
    last_scan_seconds: float
    last_refreshed: Optional[str] = None
    next_refresh_at: Optional[str] = None


class RefreshAcceptedResponse(BaseModel):
    """Confirmation returned when a manual refresh is accepted."""
    message: str = "Scan initiated"
    market: str
    timestamp: str


class SettingsResponse(BaseModel):
    """Current runtime settings."""
    refresh_interval_sec: int


class SettingsUpdateRequest(BaseModel):
    """Request to update settings."""
    refresh_interval_sec: int = Field(ge=30, le=300)

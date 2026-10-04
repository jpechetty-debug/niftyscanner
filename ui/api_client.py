"""HTTP Client for Streamlit UI communicating strictly via API_BASE_URL.

INVARIANT PER SECTION 18:
The UI talks ONLY to FastAPI via API_BASE_URL (httpx).
It never imports 'app' internals and never triggers scans directly.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple
import httpx


class ScreenerApiClient:
    """Client for calling Stock Screener REST endpoints over HTTP."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 10.0) -> None:
        self.base_url = (base_url or os.getenv("API_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.timeout = timeout

    def get_results(self, market: str = "NSE") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Fetch latest screening results for a market."""
        url = f"{self.base_url}/api/results"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.get(url, params={"market": market})
                if res.status_code == 200:
                    return res.json(), None
                elif res.status_code == 404:
                    return None, f"Market '{market}' is currently disabled in backend."
                elif res.status_code == 422:
                    return None, f"Invalid market '{market}' requested."
                else:
                    return None, f"HTTP Error {res.status_code}: {res.text}"
        except httpx.ConnectError:
            return None, f"Could not connect to API server at {self.base_url}. Is FastAPI running?"
        except Exception as e:
            return None, f"Network error fetching results: {e}"

    def get_performance(self, market="NSE", horizon=5, start=None, end=None, strategy=None, return_basis="gross"):
        params = {"market": market, "horizon": horizon, "return_basis": return_basis}
        params.update({k: str(v) for k, v in {"start": start, "end": end, "strategy": strategy}.items() if v is not None})
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(f"{self.base_url}/api/performance", params=params)
                if response.status_code == 200:
                    return response.json(), None
                return None, f"Performance unavailable (HTTP {response.status_code})."
        except Exception as error:
            return None, f"Unable to load performance: {error}"

    def get_status(self, market: str = "NSE") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Fetch overall system status, breaker states, and staleness."""
        url = f"{self.base_url}/api/status"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.get(url, params={"market": market})
                if res.status_code == 200:
                    return res.json(), None
                return None, f"HTTP Error {res.status_code}: {res.text}"
        except Exception as e:
            return None, f"Error connecting to API status endpoint: {e}"

    def post_refresh(self, market: str = "NSE") -> Tuple[bool, Optional[str], Optional[int]]:
        """Trigger an immediate screening scan.

        Returns:
            Tuple of (success: bool, message: Optional[str], retry_after: Optional[int])
        """
        url = f"{self.base_url}/api/refresh"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(url, params={"market": market})
                if res.status_code == 202:
                    return True, "Scan successfully scheduled.", None
                elif res.status_code == 429:
                    retry_after = res.headers.get("Retry-After")
                    sec = int(retry_after) if retry_after and retry_after.isdigit() else 30
                    return False, f"Scan in progress or cooldown active. Please wait {sec}s.", sec
                else:
                    return False, f"Refresh failed ({res.status_code}): {res.text}", None
        except Exception as e:
            return False, f"Error requesting refresh: {e}", None

    def get_settings(self) -> Tuple[Optional[int], Optional[str]]:
        """Retrieve current refresh interval in seconds."""
        url = f"{self.base_url}/api/settings"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    return data.get("refresh_interval_sec"), None
                return None, f"Failed to get settings: {res.text}"
        except Exception as e:
            return None, f"Error connecting to settings endpoint: {e}"

    def put_settings(self, refresh_interval_sec: int) -> Tuple[bool, Optional[str]]:
        """Update runtime refresh interval."""
        url = f"{self.base_url}/api/settings"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.put(url, json={"refresh_interval_sec": refresh_interval_sec})
                if res.status_code == 200:
                    return True, "Settings updated successfully."
                elif res.status_code == 422:
                    return False, "Invalid interval. Must be between 30 and 300 seconds."
                return False, f"Failed to update settings: {res.text}"
        except Exception as e:
            return False, f"Error updating settings: {e}"

    def get_export_csv(self, market: str = "NSE") -> Tuple[Optional[str], Optional[str]]:
        """Download raw CSV text for qualified results."""
        url = f"{self.base_url}/api/export.csv"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.get(url, params={"market": market})
                if res.status_code == 200:
                    return res.text, None
                return None, f"Failed to export CSV: {res.text}"
        except Exception as e:
            return None, f"Error downloading CSV: {e}"

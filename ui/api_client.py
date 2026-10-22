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

    def get_swing(self, path: str, **params):
        """S5 reads use HTTP exclusively, including CSV and observation charts."""
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(f"{self.base_url}/api/swing/{path.lstrip('/')}",
                                      params={k: v for k, v in params.items() if v is not None})
            if response.status_code == 200:
                return (response.text if path.endswith(".csv") else response.json()), None
            messages = {404: "Research item unavailable or feature disabled.", 409: "Evidence or version check failed. Reload and review.",
                        422: "Check the selected inputs.", 503: "Research store unavailable. Import real evidence first."}
            return None, messages.get(response.status_code, f"Research service unavailable (HTTP {response.status_code}).")
        except Exception:
            return None, "Cannot reach the research API. Check the local service."

    def post_swing(self, path: str, body: dict):
        """Returns data/error/retry seconds; callers retain the same key after uncertainty."""
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/api/swing/{path.lstrip('/')}", json=body)
            if response.status_code in {200, 202}:
                return response.json(), None, None
            if response.status_code >= 500:
                return None, "Submission status unknown. Retry the same saved request key; do not create a new command.", None
            retry = int(response.headers.get("Retry-After", "0")) or None
            messages = {404: "Research mutations are disabled or the item is unavailable.",
                        409: "State changed or work is paused. Reload and review before retrying.",
                        422: "Check the account, dates, capital and required fields.", 429: "Shared worker busy; no control was applied."}
            return None, messages.get(response.status_code, f"Research request failed (HTTP {response.status_code})."), retry
        except Exception:
            return None, "Submission status unknown. Retry the same saved request key; do not create a new command.", None

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

    def get_score_buckets(self, market: str = "NSE", horizon: int = 5):
        """Resolved outcomes per score band; (None, message) when unavailable."""
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(f"{self.base_url}/api/performance/score-buckets",
                                      params={"market": market, "horizon": horizon})
                if response.status_code == 200:
                    return response.json(), None
                return None, f"Track record unavailable (HTTP {response.status_code})."
        except Exception as error:
            return None, f"Unable to load track record: {error}"

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

    def get_macro_overview(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Fetch macro market indicators (India VIX, Brent Crude, Nifty 50) over HTTP."""
        url = f"{self.base_url}/api/macro"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.get(url)
                if res.status_code == 200:
                    return res.json(), None
                return None, f"Macro API returned HTTP {res.status_code}"
        except Exception as e:
            return None, f"Cannot reach macro indicators: {e}"

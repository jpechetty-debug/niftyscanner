"""Unit and architectural tests for Streamlit UI and HTTP API Client."""

import ast
from pathlib import Path
import pandas as pd
import pytest

from ui.api_client import ScreenerApiClient
from ui.app import filter_results_dataframe


def test_ui_strict_architectural_decoupling():
    """Verify that files in ui/ NEVER import from 'app' internals per Section 18."""
    ui_dir = Path("ui")
    py_files = list(ui_dir.glob("*.py"))
    assert len(py_files) > 0, "No Python files found in ui/"

    forbidden_imports = []

    for f in py_files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app" or alias.name.startswith("app."):
                        forbidden_imports.append((f.name, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module == "app" or node.module.startswith("app.")):
                    forbidden_imports.append((f.name, node.module))

    assert (
        len(forbidden_imports) == 0
    ), f"Architectural violation: UI files imported app internals: {forbidden_imports}"


def test_ui_filter_results_dataframe():
    """Verify client-side interactive filtering on results table."""
    sample_data = [
        {
            "ticker": "INFY.NS",
            "name": "Infosys Limited",
            "rsi": 65.0,
            "volume_ratio": 3.0,
            "pe": 18.0,
            "session_partial": False,
        },
        {
            "ticker": "TCS.NS",
            "name": "Tata Consultancy Services",
            "rsi": 45.0,
            "volume_ratio": 2.5,
            "pe": 22.0,
            "session_partial": True,
        },
        {
            "ticker": "RELIANCE.NS",
            "name": "Reliance Industries",
            "rsi": 70.0,
            "volume_ratio": 4.5,
            "pe": 16.0,
            "session_partial": True,
        },
    ]
    df = pd.DataFrame(sample_data)

    # 1. Text search by ticker or company name
    f_infy = filter_results_dataframe(
        df, search_query="infy", min_rsi=0, min_vol_ratio=0, max_pe=100, partial_only=False
    )
    assert len(f_infy) == 1
    assert f_infy.iloc[0]["ticker"] == "INFY.NS"

    f_tata = filter_results_dataframe(
        df, search_query="tata", min_rsi=0, min_vol_ratio=0, max_pe=100, partial_only=False
    )
    assert len(f_tata) == 1
    assert f_tata.iloc[0]["ticker"] == "TCS.NS"

    # 2. Filter by RSI >= 60 -> INFY and RELIANCE
    f_rsi = filter_results_dataframe(
        df, search_query="", min_rsi=60.0, min_vol_ratio=0, max_pe=100, partial_only=False
    )
    assert len(f_rsi) == 2

    # 3. Filter by partial_only -> TCS and RELIANCE
    f_partial = filter_results_dataframe(
        df, search_query="", min_rsi=0, min_vol_ratio=0, max_pe=100, partial_only=True
    )
    assert len(f_partial) == 2
    assert set(f_partial["ticker"]) == {"TCS.NS", "RELIANCE.NS"}


def test_screener_api_client_error_handling(monkeypatch):
    """Test ScreenerApiClient handling of HTTP responses, 429 Retry-After, and network drops."""
    client = ScreenerApiClient(base_url="http://mock-api.local")

    # Mock httpx responses using a dummy client
    class MockResponse:
        def __init__(self, status_code, json_data=None, text="", headers=None):
            self.status_code = status_code
            self._json = json_data or {}
            self.text = text
            self.headers = headers or {}

        def json(self):
            return self._json

    class MockHttpxClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, params=None):
            if "status" in url:
                return MockResponse(200, {"market_status": {"is_open": True}})
            elif "results" in url:
                if params and params.get("market") == "NYSE":
                    return MockResponse(404, text="Market disabled")
                return MockResponse(200, {"meta": {}, "results": []})
            elif "settings" in url:
                return MockResponse(200, {"refresh_interval_sec": 60})
            elif "export.csv" in url:
                return MockResponse(200, text="ticker,price\nTEST,100")
            return MockResponse(404)

        def post(self, url, params=None):
            if "refresh" in url:
                return MockResponse(429, headers={"Retry-After": "25"})
            return MockResponse(400)

        def put(self, url, json=None):
            if "settings" in url:
                if json and json.get("refresh_interval_sec") == 120:
                    return MockResponse(200)
                return MockResponse(422)
            return MockResponse(400)

    import httpx
    monkeypatch.setattr(httpx, "Client", MockHttpxClient)

    # 1. Test get_results (success & 404 disabled)
    res_ok, err_ok = client.get_results("NSE")
    assert err_ok is None
    assert "results" in res_ok

    res_nyse, err_nyse = client.get_results("NYSE")
    assert res_nyse is None
    assert "disabled" in err_nyse.lower()

    # 2. Test post_refresh 429 with Retry-After
    success, msg, retry_sec = client.post_refresh("NSE")
    assert success is False
    assert retry_sec == 25
    assert "25s" in msg

    # 3. Test get_settings and put_settings
    curr_int, _ = client.get_settings()
    assert curr_int == 60

    ok_put, _ = client.put_settings(120)
    assert ok_put is True

    bad_put, msg_bad = client.put_settings(20)
    assert bad_put is False
    assert "between 30 and 300" in msg_bad

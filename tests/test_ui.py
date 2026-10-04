"""Unit and architectural tests for Streamlit UI and HTTP API Client."""

import ast
from pathlib import Path
import pandas as pd
import pytest

from ui.api_client import ScreenerApiClient
from ui.app import filter_results_dataframe, format_scan_time


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


def test_optional_filters_preserve_every_qualified_result():
    """UI defaults must not silently re-screen backend results or cap volume."""
    import json
    response = json.loads(Path("tests/fixtures/ui_results_synthetic.json").read_text())
    frame = pd.DataFrame(response["results"])
    result = filter_results_dataframe(frame, "", None, None, None, False)
    pd.testing.assert_frame_equal(frame, result)
    assert len(filter_results_dataframe(frame, "", None, None, 0, False)) == 1


def test_exchange_local_scan_times():
    assert "11:00 IST" in format_scan_time("2026-10-01T05:30:00+00:00", "NSE")
    assert "09:30 EDT" in format_scan_time("2026-10-01T13:30:00+00:00", "NYSE")
    assert format_scan_time(None, "NSE") == "Awaiting first scan"
    assert format_scan_time("invalid", "NSE") == "Time unavailable"


def test_light_workspace_search_sort_and_empty_state(monkeypatch):
    """Exercise actual Streamlit controls with labelled test-only API responses."""
    import importlib
    import json
    from streamlit.testing.v1 import AppTest

    monkeypatch.syspath_prepend(str(Path("ui").resolve()))
    client = importlib.import_module("api_client").ScreenerApiClient
    response = json.loads(Path("tests/fixtures/ui_results_synthetic.json").read_text())
    monkeypatch.setattr(client, "get_results", lambda *a, **k: (response, None))
    monkeypatch.setattr(client, "get_status", lambda *a, **k: ({
        "market_status": {"is_open": False, "is_holiday": True}, "is_scanning": False}, None))
    monkeypatch.setattr(client, "get_settings", lambda *a, **k: (60, None))
    page = AppTest.from_file("ui/app.py").run()
    assert not page.exception
    assert len(page.dataframe[0].value) == 3
    assert page.dataframe[0].value.iloc[0]["ticker"] == "TEST2.NS"
    assert page.warning and "outdated" in page.warning[0].value
    assert any("11:00 IST" in item.value for item in page.markdown if 'notice-timestamp' in item.value)
    page.selectbox(key="sort_NSE").select("P/E").run()
    assert not page.exception
    assert page.dataframe[0].value.iloc[0]["ticker"] == "TEST1.NS"
    page.toggle[0].set_value(True).run()
    page.slider[0].set_value(60.0).run()
    assert not page.exception
    assert set(page.dataframe[0].value["ticker"]) == {"TEST2.NS", "TEST3.NS"}
    page.toggle[0].set_value(False).run()
    assert len(page.dataframe[0].value) == 3
    page.text_input[0].set_value("SYNTHETIC Gamma").run()
    assert not page.exception
    assert page.dataframe[0].value["ticker"].tolist() == ["TEST3.NS"]
    page.text_input[0].set_value("no-match").run()
    assert not page.exception
    assert any("No stocks match your view" in info.value for info in page.info)
    assert not page.dataframe


@pytest.fixture
def terminal_page(monkeypatch):
    """Build a Streamlit test session from the labelled SYNTHETIC fixture only."""
    import importlib
    import json
    from streamlit.testing.v1 import AppTest

    monkeypatch.syspath_prepend(str(Path("ui").resolve()))
    client = importlib.import_module("api_client").ScreenerApiClient
    response = json.loads(Path("tests/fixtures/ui_results_synthetic.json").read_text())
    monkeypatch.setattr(client, "get_results", lambda *a, **k: (response, None))
    monkeypatch.setattr(client, "get_status", lambda *a, **k: ({
        "market_status": {"is_open": False, "is_holiday": True}, "is_scanning": False}, None))
    monkeypatch.setattr(client, "get_settings", lambda *a, **k: (60, None))
    return AppTest.from_file("ui/app.py"), response, client


def test_terminal_pagination_sort_filter_reset_and_cards(terminal_page):
    """Pagination never hides matches after a filter change; card/table views agree."""
    page, response, _ = terminal_page
    for index in range(4, 11):
        response["results"].append({**response["results"][0],
                                    "ticker": f"TEST{index}.NS", "name": f"SYNTHETIC company {index}"})
    page.run()
    assert not page.exception
    first_page = page.dataframe[0].value["ticker"].tolist()
    assert len(first_page) == 6
    assert page.button(key="previous_NSE").disabled
    page.button(key="next_NSE").click().run()
    assert not page.exception
    assert len(page.dataframe[0].value) == 4
    assert not set(first_page) & set(page.dataframe[0].value["ticker"])
    assert page.button(key="next_NSE").disabled
    page.selectbox(key="sort_NSE").select("P/E").run()
    assert page.selectbox(key="page_NSE").value == 1
    page.text_input(key="search").set_value("SYNTHETIC Beta").run()
    assert page.dataframe[0].value["ticker"].tolist() == ["TEST2.NS"]
    page.radio(key="layout_NSE").set_value("Cards").run()
    assert not page.exception
    assert not page.dataframe
    assert any('TEST2.NS' in item.value and 'result-card' in item.value for item in page.markdown)
    next(button for button in page.button if button.label == "Reset filters").click().run()
    assert page.text_input(key="search").value == ""
    page.radio(key="layout_NSE").set_value("Table").run()
    assert len(page.dataframe[0].value) == 6


def test_terminal_escapes_api_text_and_preserves_partial_empty_state(terminal_page):
    """Remote names cannot inject markup, and closed-session filters explain empty views."""
    page, response, _ = terminal_page
    response["results"][1]["name"] = '<img src=x onerror="alert(1)"> & company'
    page.run()
    assert not page.exception
    spotlight = next(item.value for item in page.markdown if 'HIGHEST RANKED' in item.value)
    assert '&lt;img' in spotlight and '&amp; company' in spotlight
    assert '<img' not in spotlight
    page.checkbox(key="partial_only").set_value(True).run()
    assert not page.dataframe
    assert any("No stocks match your view" in item.value for item in page.info)


def test_terminal_busy_scan_and_cooldown_feedback(terminal_page, monkeypatch):
    """Busy scans disable refresh; API cooldown feedback exposes the real retry delay."""
    page, _, client = terminal_page
    monkeypatch.setattr(client, "get_status", lambda *a, **k: ({
        "market_status": {"is_open": True}, "is_scanning": True}, None))
    page.run()
    scan = next(button for button in page.button if button.label == "Scan now")
    assert scan.disabled
    monkeypatch.setattr(client, "get_status", lambda *a, **k: ({
        "market_status": {"is_open": False}, "is_scanning": False}, None))
    monkeypatch.setattr(client, "post_refresh", lambda *a, **k: (False, "Cooldown active.", 25))
    page.run()
    next(button for button in page.button if button.label == "Scan now").click().run()
    assert not page.exception
    assert any("25s" in item.value for item in page.get("toast"))
    monkeypatch.setattr(client, "get_results", lambda *a, **k: (None, "API unavailable"))
    page.run()
    assert any("API unavailable" in item.value for item in page.error)


def test_terminal_header_connection_and_execution_action(terminal_page, monkeypatch):
    """The new execution button uses HTTP; disconnected headers disable scans."""
    page, _, client = terminal_page
    requests = []

    def refresh(*args, **kwargs):
        requests.append(kwargs["market"])
        return False, "Cooldown active.", 12

    monkeypatch.setattr(client, "post_refresh", refresh)
    page.run()
    assert any('CONNECTED' in item.value and 'terminal-bar' in item.value for item in page.markdown)
    next(button for button in page.button if button.label == "Run scan").click().run()
    assert not page.exception
    assert requests == ["NSE"]
    assert any("12s" in item.value for item in page.get("toast"))
    monkeypatch.setattr(client, "get_status", lambda *a, **k: (None, "API unavailable"))
    page.run()
    assert not page.exception
    assert any('DISCONNECTED' in item.value and 'terminal-bar' in item.value for item in page.markdown)
    assert next(button for button in page.button if button.label == "Scan now").disabled
    assert not any(button.label == "Run scan" for button in page.button)


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

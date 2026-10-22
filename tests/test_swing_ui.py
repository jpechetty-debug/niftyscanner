"""SYNTHETIC UI mocks and HTTP transport checks; no live market/provider requests."""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import json

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from app.swing.setups import evaluate
from app.swing.workspace import filtered_plans, plans_csv
from tests.fixtures.swing_setups import setup_fixture
from tests.fixtures.swing_ui import ui_payloads
from ui.api_client import ScreenerApiClient
from ui.swing import chart_figure, idea_card


def test_idea_filters_and_export_use_the_same_stable_plan_view():
    cfg, cal, now, stock, benchmark, evidence = setup_fixture()
    primary = evaluate("TEST1.NS", stock, benchmark, evidence, cfg, cal, now).plans[0]
    other = replace(primary, id="SYNTHETIC-other", ticker="TEST2.NS")
    names = {"TEST1.NS": "SYNTHETIC Company One", "TEST2.NS": "SYNTHETIC Company Two"}
    view = filtered_plans([other, primary], search="company one", names=names)
    assert [p.ticker for p in view] == ["TEST1.NS"]
    assert filtered_plans([primary], strategy="DAILY_PULLBACK_V1") == []
    text = plans_csv(view, names)
    import csv, io
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["ticker"] == "TEST1.NS" and rows[0]["quantity"] == ""
    assert rows[0]["name"] == names["TEST1.NS"] and rows[0]["model_status"] == "RESEARCH_ONLY"
    assert "TEST2.NS" not in text
    assert "'=SYNTHETIC()" in plans_csv([primary], {"TEST1.NS": "=SYNTHETIC()"})


def test_cards_escape_source_text_and_do_not_invent_position_size():
    payloads = ui_payloads()
    plan = payloads["workspace"]["research"]["plans"][0]
    html = idea_card(plan, '<script>"SYNTHETIC"</script>')
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "Size requires a reconciled account" in html and "Research only" in html


def test_unknown_or_mismatched_chart_basis_never_overlays_executable_levels():
    payloads = ui_payloads()
    plan = payloads["workspace"]["research"]["plans"][0]
    chart = payloads["chart"]
    assert len(chart_figure(chart, plan).layout.shapes) == 0
    chart["price_basis"] = plan["price_basis"]
    assert len(chart_figure(chart, plan).layout.shapes) == 4
    chart["vintage"] = "2"*64
    assert len(chart_figure(chart, plan).layout.shapes) == 0
    assert chart_figure({"observations": []}) is None


@pytest.fixture
def ui_mock(monkeypatch):
    payloads = ui_payloads()
    calls = []
    def read(self, path, **params):
        calls.append((path, params))
        data = deepcopy(payloads.get(path))
        if path == "workspace" and params.get("search"):
            data["research"]["plans"] = []
        return data, None
    monkeypatch.setattr(ScreenerApiClient, "get_swing", read)
    return payloads, calls


HARNESS = '''
import streamlit as st
from ui.api_client import ScreenerApiClient
from ui.swing import render_swing
st.set_page_config(layout="wide")
render_swing(ScreenerApiClient(), lambda market, connected: st.caption("SYNTHETIC HTTP-client UI test"))
'''


def test_trade_ideas_filters_empty_state_and_disabled_funding(ui_mock):
    payloads, calls = ui_mock
    page = AppTest.from_string(HARNESS).run()
    assert not page.exception
    assert any("TEST1.NS" in m.value for m in page.markdown)
    assert page.text_input(key="swing_account_capital").value == ""
    page.text_input(key="swing_search").set_value("no match").run()
    assert not page.exception
    assert any("No usable swing ideas yet" in m.value for m in page.markdown)
    assert any(path == "workspace" and params["search"] == "no match" for path, params in calls)


def test_source_chart_and_validation_empty_views(ui_mock):
    payloads, calls = ui_mock
    page = AppTest.from_string(HARNESS).run()
    page.selectbox(key="swing_source_ticker").select("TEST1.NS").run()
    assert not page.exception
    assert any(path == "chart" for path, _ in calls)
    assert any("No real evaluation report" in x.value for x in page.info)
    assert any("Price basis: Unknown" in x.value for x in page.caption)
    assert any("No prospective campaign" in x.value for x in page.info)


def test_forward_monitor_displays_unresolved_sessions_without_mutating(ui_mock, monkeypatch):
    payloads, calls = ui_mock
    payloads["forward/campaigns"] = [{"campaign": "SYNTHETIC-campaign", "captured_sessions": 2,
        "reconciled_sessions": 0, "minimum_sessions": 30, "start": "2026-10-19", "end": "2026-12-01",
        "signal_end": "2026-11-10", "failures": ["opening_capacity_unknown"],
        "missing_sessions": ["2026-10-20"], "days": [{"comparisons": [{"ticker": "TEST1.NS", "status": "UNRESOLVED"}]}]}]
    def forbid_post(*a, **k):
        raise AssertionError("Reading forward validation must not submit a mutation")
    monkeypatch.setattr(ScreenerApiClient, "post_swing", forbid_post)
    page = AppTest.from_string(HARNESS).run()
    assert not page.exception
    assert any("Missing actual session captures" in w.value for w in page.warning)
    assert any("Promotion blocked" in c.value for c in page.caption)
    assert any(m.label == "Captured sessions" and m.value == "2" for m in page.metric)


def test_unavailable_api_does_not_render_mutation_controls(monkeypatch):
    monkeypatch.setattr(ScreenerApiClient, "get_swing", lambda *a, **k: (None, "SYNTHETIC service unavailable"))
    page = AppTest.from_string(HARNESS).run()
    assert not page.exception
    assert page.error and not page.button


def test_unknown_control_response_retains_identical_request_key(ui_mock, monkeypatch):
    sent = []
    def post(self, path, body):
        sent.append((path, deepcopy(body)))
        return None, "Submission status unknown. Retry the same saved request key.", None
    monkeypatch.setattr(ScreenerApiClient, "post_swing", post)
    page = AppTest.from_string(HARNESS).run()
    page.button(key="swing_pause").click().run()
    assert not page.exception and len(sent) == 1
    page.button(key="swing_retry_saved").click().run()
    assert len(sent) == 2 and sent[0] == sent[1]


def test_swing_http_client_uses_explicit_vintage_and_preserves_post_key(monkeypatch):
    seen = []
    def handle(request):
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(503)
        return httpx.Response(200, json={"SYNTHETIC": True})
    original = httpx.Client
    monkeypatch.setattr("ui.api_client.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    client = ScreenerApiClient("http://SYNTHETIC.local")
    assert client.get_swing("workspace", vintage="1"*64)[0] == {"SYNTHETIC": True}
    assert seen[-1].url.params["vintage"] == "1"*64
    data, error, _ = client.post_swing("operations", {"key": "SYNTHETIC-stable", "paused": True, "expected_revision": 0})
    assert data is None and error.startswith("Submission status unknown")
    assert json.loads(seen[-1].content)["key"] == "SYNTHETIC-stable"

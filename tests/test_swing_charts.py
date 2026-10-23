"""SYNTHETIC recorded chart evidence and HTTP-payload-only rendering tests."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
import json

import pytest

from app.swing.api import ResearchResponse
from app.swing.charts import breakout_chart_context
from app.swing.setups import evaluate
from tests.fixtures.swing_setups import setup_fixture
from tests.test_swing_explanations import service_fixture
from ui.swing_charts import chart_context_captions, chart_figure, matched_breakout_context


def recorded_fixture(**changes):
    cfg, cal, now, stock, benchmark, evidence = setup_fixture()
    if changes:
        cfg = type(cfg)(_env_file=None, **{**cfg.model_dump(), **changes})
    result = evaluate("TEST1.NS", stock, benchmark, evidence, cfg, cal, now)
    plan = next(p for p in result.plans if p.strategy == "DAILY_BREAKOUT_V1")
    return cfg, stock, result, plan, breakout_chart_context(plan, stock, result.checks)


def payload_fixture():
    cfg, stock, result, plan, context = recorded_fixture()
    encoded_plan = json.loads(json.dumps(asdict(plan), default=str))
    encoded_context = json.loads(json.dumps(asdict(context), default=str))
    payload = {"ticker": plan.ticker, "price_basis": plan.price_basis, "vintage": plan.evidence.vintage,
               "observations": [r.model_dump(mode="json") for r in stock[-40:]], "missing_sessions": []}
    return payload, encoded_plan, encoded_context


@pytest.mark.parametrize("n,v", [(20, 20), (7, 9), (30, 12)])
def test_context_records_exact_prior_windows_and_existing_rule_values(n, v):
    cfg, stock, result, plan, context = recorded_fixture(SWING_BREAKOUT_LOOKBACK=n, SWING_VOLUME_LOOKBACK=v)
    checks = {c.rule: c for c in result.checks if c.strategy == plan.strategy}
    assert context.plan_id == plan.id and context.data_hash == plan.data_hash
    assert context.config_hash == plan.config_hash and context.decision_at == plan.decision_at
    assert (context.resistance.start, context.resistance.end, context.resistance.sessions) == (stock[-n-1].session, stock[-2].session, n)
    assert context.resistance.level == checks["breakout_close"].threshold == max(r.high for r in stock[-n-1:-1])
    assert (context.volume.start, context.volume.end, context.volume.sessions) == (stock[-v-1].session, stock[-2].session, v)
    assert context.volume.average_volume == pytest.approx(sum(r.volume for r in stock[-v-1:-1])/v)
    assert context.volume.average_volume == plan.features.average_volume
    assert context.volume.signal_volume == stock[-1].volume
    assert context.volume.relative_volume == plan.features.volume_ratio
    assert context.volume.minimum_relative_volume == checks["breakout_volume"].threshold
    assert context.extension.atr == plan.features.atr
    assert context.extension.distance == checks["breakout_extension"].observed
    assert context.extension.multiple == context.extension.distance/plan.features.atr
    assert context.extension.maximum_close == context.resistance.level+checks["breakout_extension"].threshold
    assert context.extension.ema_fast_multiple == (plan.features.close-plan.features.ema_fast)/plan.features.atr
    assert context.extension.maximum_multiple == dict(plan.configuration)["SWING_BREAKOUT_EXTENSION_ATR"]


def test_context_ignores_future_bars_and_never_backfills_missing_old_checks():
    cfg, stock, result, plan, context = recorded_fixture()
    future = stock[-1].model_copy(update={"session": plan.session+timedelta(days=1), "high": 9999, "volume": 999999999})
    assert breakout_chart_context(plan, (*stock, future), result.checks) == context
    assert breakout_chart_context(plan, stock, ()) is None
    assert breakout_chart_context(replace(plan, strategy="DAILY_PULLBACK_V1"), stock, result.checks) is None
    assert breakout_chart_context(plan, stock[-2:], result.checks) is None
    bad = tuple(replace(c, threshold=float("nan")) if c.rule == "breakout_close" else c for c in result.checks)
    assert breakout_chart_context(plan, stock, bad) is None


@pytest.mark.parametrize("unavailable", [True, False])
def test_service_and_typed_response_add_context_without_changing_old_payloads(monkeypatch, unavailable):
    response = service_fixture(monkeypatch, unavailable=unavailable).results("1"*64)
    encoded = json.loads(ResearchResponse(**response).model_dump_json())
    assert bool(encoded["chart_contexts"]) is (not unavailable)
    if not unavailable:
        context = encoded["chart_contexts"][0]
        plan = next(p for p in encoded["plans"] if p["id"] == context["plan_id"])
        assert context["ticker"] == plan["ticker"] and context["vintage"] == plan["evidence"]["vintage"]
        assert context["session"] == plan["session"]
    response.pop("chart_contexts")
    assert ResearchResponse(**response).chart_contexts == []


def test_chart_has_frozen_resistance_volume_atr_and_preserves_four_plan_levels():
    payload, plan, context = payload_fixture()
    original = deepcopy((payload, plan, context))
    figure = chart_figure(payload, plan, context)
    traces = {trace.name: trace for trace in figure.data}
    shapes = {shape.name: shape for shape in figure.layout.shapes}
    for key in ("stop", "target", "entry_min", "entry_max"):
        assert shapes[key].y0 == shapes[key].y1 == plan[key]
    window = shapes["Recorded resistance window"]
    assert window.x0 == context["resistance"]["start"] and window.x1 == context["resistance"]["end"]
    assert tuple(traces["Recorded resistance"].y) == (context["resistance"]["level"],)*2
    assert tuple(traces["Breakout close ceiling"].y) == (context["extension"]["maximum_close"],)*2
    assert shapes["Breakout ATR allowance"].y0 == context["resistance"]["level"]
    assert tuple(traces["Recorded volume baseline"].y) == (context["volume"]["average_volume"],)*2
    assert traces["Observed volume"].yaxis == traces["Recorded volume baseline"].yaxis == "y2"
    assert figure.layout.yaxis2.title.text == "Volume (shares)"
    assert list(traces["Observed volume"].y) == [r["volume"] for r in payload["observations"]]
    assert "Close − resistance" in chart_context_captions(payload, plan, context)[2]
    assert "Close − fast EMA" in chart_context_captions(payload, plan, context)[2]
    figure.to_json()
    assert (payload, plan, context) == original


@pytest.mark.parametrize("key,value", [("ticker", "TEST2.NS"), ("vintage", "wrong"), ("price_basis", "unknown")])
def test_chart_identity_mismatch_suppresses_all_plan_overlays(key, value):
    payload, plan, context = payload_fixture()
    payload[key] = value
    assert matched_breakout_context(payload, plan, context) is None
    assert not chart_figure(payload, plan, context).layout.shapes
    assert "does not match" in chart_context_captions(payload, plan, context)[0]


@pytest.mark.parametrize("key", ["plan_id", "ticker", "vintage", "price_basis", "session", "decision_at", "config_hash", "data_hash"])
def test_context_cannot_cross_plan_identity_or_hashes(key):
    payload, plan, context = payload_fixture()
    context[key] = "SYNTHETIC-mismatch"
    assert matched_breakout_context(payload, plan, context) is None
    assert len(chart_figure(payload, plan, context).layout.shapes) == 4
    assert "context unavailable" in chart_context_captions(payload, plan, context)[0]


@pytest.mark.parametrize("invalid", ["missing", "signal_in_window", "nonfinite", "zero_atr", "invalid_window"])
def test_missing_or_invalid_context_remains_explicitly_unavailable(invalid):
    payload, plan, context = payload_fixture()
    if invalid == "missing":
        context.pop("volume")
    elif invalid == "signal_in_window":
        context["resistance"]["end"] = plan["session"]
    elif invalid == "nonfinite":
        context["volume"]["average_volume"] = float("nan")
    elif invalid == "zero_atr":
        context["extension"]["atr"] = 0
    else:
        context["resistance"]["start"] = "SYNTHETIC-invalid-date"
    assert matched_breakout_context(payload, plan, context) is None
    assert len(chart_figure(payload, plan, context).layout.shapes) == 4
    assert len(chart_context_captions(payload, plan, context)) == 1
    assert len(chart_figure(payload, plan).layout.shapes) == 4


def test_chart_subset_and_missing_values_cannot_recalculate_recorded_indicators():
    payload, plan, context = payload_fixture()
    payload["observations"] = payload["observations"][-5:]
    payload["observations"][0]["volume"] = None
    payload["observations"][-1]["high"] = 9999
    payload["observations"][-1]["volume"] = 1
    figure = chart_figure(payload, plan, context)
    traces = {trace.name: trace for trace in figure.data}
    shapes = {shape.name: shape for shape in figure.layout.shapes}
    assert shapes["Recorded resistance window"].x0 == payload["observations"][0]["session"]
    assert tuple(traces["Recorded resistance"].y) == (context["resistance"]["level"],)*2
    assert tuple(traces["Recorded volume baseline"].y) == (context["volume"]["average_volume"],)*2
    assert traces["Observed volume"].y[0] is None
    assert tuple(traces["Signal close"].y) == (plan["features"]["close"],)
    assert context["resistance"]["start"] in chart_context_captions(payload, plan, context)[0]


def test_source_and_pullback_charts_do_not_inherit_breakout_context():
    payload, plan, context = payload_fixture()
    assert len(chart_figure(payload).data) == 1
    assert not chart_figure(payload).layout.shapes
    plan["strategy"] = "DAILY_PULLBACK_V1"
    assert len(chart_figure(payload, plan, context).layout.shapes) == 4
    assert chart_context_captions(payload, plan, context) == ()
    assert chart_figure({"observations": []}, plan, context) is None

"""SYNTHETIC-only rule, read-contract and empty-view regression checks."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from app.swing.api import ResearchResponse, read
from app.swing.explanations import record_check, view_summary
from app.swing.models import HistoryResult, DatedContext
from app.swing.service import SwingResearchService
from app.swing.setups import evaluate
from app.swing.workspace import SwingWorkspace
from tests.fixtures.swing_setups import setup_fixture
from tests.fixtures.swing_ui import ui_payloads


def evaluate_fixture(kind="breakout", changes=None):
    cfg, cal, now, stock, benchmark, evidence = setup_fixture(kind)
    if changes:
        cfg = type(cfg)(_env_file=None, **{**cfg.model_dump(), **changes})
    return evaluate("TEST1.NS", stock, benchmark, evidence, cfg, cal, now)


def test_breakout_evidence_reports_actual_unrounded_operands_and_outcomes():
    result = evaluate_fixture()
    by_rule = {check.rule: check for check in result.checks if check.strategy == "DAILY_BREAKOUT_V1"}
    volume = by_rule["breakout_volume"]
    assert volume.observed == 2.0 and volume.threshold == 1.5
    assert volume.operator == ">=" and volume.passed is True
    assert by_rule["breakout_close"].observed == 140.9
    assert by_rule["breakout_close"].threshold == pytest.approx(140.6)
    assert by_rule["breakout_close"].operator == ">"
    plan = next(p for p in result.plans if p.strategy == "DAILY_BREAKOUT_V1")
    assert by_rule["net_reward_risk"].observed[0] == plan.net_rr
    assert by_rule["net_reward_risk"].observed[1] == plan.entry_max
    assert by_rule["net_reward_risk"].threshold[1] == plan.entry_min
    assert "checks" not in asdict(plan) and "rule_explanations" not in asdict(plan)
    json.dumps([asdict(c) for c in result.checks], allow_nan=False)


def test_inclusive_volume_and_strict_resistance_boundaries_are_preserved():
    inclusive = evaluate_fixture(changes={"SWING_BREAKOUT_RVOL": 2})
    assert next(c for c in inclusive.checks if c.rule == "breakout_volume").passed
    cfg, cal, now, stock, benchmark, evidence = setup_fixture()
    resistance = max(r.high for r in stock[-cfg.SWING_BREAKOUT_LOOKBACK-1:-1])
    stock = (*stock[:-1], stock[-1].model_copy(update={"close": resistance}))
    exact = evaluate("TEST1.NS", stock, benchmark, evidence, cfg, cal, now)
    check = next(c for c in exact.checks if c.rule == "breakout_close")
    assert check.observed == check.threshold and check.passed is False
    assert not any(p.strategy == "DAILY_BREAKOUT_V1" for p in exact.plans)


def test_shared_filter_captures_only_reached_stage_and_missing_data_has_no_values():
    shared = evaluate_fixture(changes={"SWING_MIN_AVG_TRADED_VALUE_INR": 1e12})
    assert not shared.plans and all(c.strategy is None for c in shared.checks)
    assert next(c for c in shared.checks if c.rule == "liquidity").passed is False
    assert all(r.category == "filtered" for r in shared.rejected)
    cfg, cal, now, stock, benchmark, evidence = setup_fixture()
    for rows, proof in ((stock[:-1], evidence), (stock, replace(evidence, available_at=now+timedelta(seconds=1)))):
        failed = evaluate("TEST1.NS", rows, benchmark, proof, cfg, cal, now)
        assert failed.checks == () and failed.rejected[0].category == "failed"


def test_compound_pullback_operands_keep_session_order_and_actual_outcome():
    result = evaluate_fixture("pullback")
    support = next(c for c in result.checks if c.rule == "pullback_support")
    assert support.operator == "all >=" and support.strategy == "DAILY_PULLBACK_V1"
    assert len(support.observed) == len(support.threshold) == 5
    assert support.passed == all(a >= b for a, b in zip(support.observed, support.threshold))
    touch = next(c for c in result.checks if c.rule == "pullback_touch")
    assert touch.passed == any(a <= b for a, b in zip(touch.observed, touch.threshold))


def test_nonfinite_evidence_stays_unavailable_in_json():
    checks = []
    record_check(checks, "SYNTHETIC", "SYNTHETIC nonfinite", (float("nan"), float("-inf")), ">=", 1, False)
    assert checks[0].observed == (None, None)
    assert checks[0].passed is False
    json.dumps(asdict(checks[0]), allow_nan=False)


@pytest.mark.parametrize("state,plans,failed,filtered,count,universe,enabled", [
    ("disabled", [], [], [], 0, 1, False),
    ("empty_universe", [], [], [], 0, 0, True),
    ("candidates", ["TEST1.NS"], [], [], 1, 1, True),
    ("view_filtered", ["TEST1.NS"], [], [], 0, 1, True),
    ("evaluation_unavailable", [], ["TEST1.NS"], [], 0, 1, True),
    ("partial_unavailable", [], ["TEST1.NS"], ["TEST2.NS"], 0, 2, True),
    ("no_matching_setups", [], [], ["TEST1.NS"], 0, 1, True),
    ("summary_unavailable", [], [], [], 0, 1, True),
])
def test_view_states_distinguish_sources_rules_and_view_filters(state, plans, failed, filtered, count, universe, enabled):
    research = {"plans": [{"ticker": t} for t in plans], "failed": [{"ticker": t} for t in failed],
                "filtered": [{"ticker": t} for t in filtered], "funnel": {"universe": universe},
                "missing_gates": ["strategy_validation", "historical_identity_unverified"]}
    result = view_summary(research, count, enabled=enabled)
    assert result["state"] == state
    assert result["failed_symbols"] == len(failed)
    assert result["filtered_symbols"] == len(filtered)
    assert result["next_action"]


def test_summary_counts_symbols_once_and_does_not_call_a_candidate_filtered():
    result = view_summary({"plans": [{"ticker": "TEST1.NS"}, {"ticker": "TEST1.NS"}],
        "failed": [{"ticker": "TEST2.NS"}, {"ticker": "TEST2.NS"}],
        "filtered": [{"ticker": "TEST1.NS"}, {"ticker": "TEST3.NS"}, {"ticker": "TEST3.NS"}]}, 2)
    assert result["total_candidates"] == 2 and result["candidate_symbols"] == 1
    assert result["failed_symbols"] == result["filtered_symbols"] == 1


def test_workspace_computes_filter_state_before_hiding_candidates():
    research = ui_payloads()["workspace"]["research"]
    research["plans"] = [evaluate_fixture().plans[0]]
    # Read-boundary stub only; no source store or production UI is created.
    workspace = SwingWorkspace.__new__(SwingWorkspace)
    workspace.config = SimpleNamespace(SWING_ENABLED=True)
    from contextlib import nullcontext
    workspace.repository = SimpleNamespace(verification_scope=nullcontext)
    workspace.snapshot = lambda vintage: ({}, {})
    workspace.names = lambda metadata, header: research["names"]
    workspace.research = SimpleNamespace(results=lambda vintage: deepcopy(research))
    result = workspace.ideas("1"*64, search="SYNTHETIC-no-match")
    assert result["plans"] == [] and result["view_count"] == 0
    assert result["view_summary"]["state"] == "view_filtered"
    assert result["view_summary"]["total_candidates"] == 1
    assert result["rule_explanations"]


def service_fixture(monkeypatch, *, unavailable=False):
    cfg, cal, now, stock, benchmark, evidence = setup_fixture()
    header = {"adapter": "SYNTHETIC", "universe_sha256": "SYNTHETIC-HASH", "members": {"TEST1.NS": {}}}
    def history(request):
        if unavailable:
            return HistoryResult("unavailable", "SYNTHETIC_missing_history")
        return HistoryResult("available", None, benchmark if request.ticker == cfg.SWING_BENCHMARK else stock)
    def context(request):
        return DatedContext(kind=request.kind, ticker=request.ticker, session=request.session,
                            source="SYNTHETIC", ingested_at=now, status="unavailable")
    service = SwingResearchService(SimpleNamespace(history=history, context=context), cfg,
                                   SimpleNamespace(now=lambda: now), cal)
    monkeypatch.setattr(service, "snapshot", lambda vintage, instant: ({"imported_at": now.isoformat()}, header))
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_service_read_and_response_serialization_preserve_real_check_availability(monkeypatch, unavailable):
    service = service_fixture(monkeypatch, unavailable=unavailable)
    result = await read(service.results, "1"*64)
    response = ResearchResponse(**result)
    encoded = json.loads(response.model_dump_json())
    explanation = encoded["rule_explanations"][0]
    assert explanation["vintage"] == encoded["vintage"]
    assert bool(explanation["checks"]) is (not unavailable)
    assert explanation["outcome"] == ("failed" if unavailable else "candidate")
    assert encoded["paper_eligible"] is False
    # Older read payloads are compatible and never reconstructed with new settings.
    result.pop("rule_explanations")
    assert ResearchResponse(**result).rule_explanations == []

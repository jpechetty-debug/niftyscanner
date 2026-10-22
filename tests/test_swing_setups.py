"""S2 causal hypotheses and independent numerical references; SYNTHETIC fixtures."""
from dataclasses import asdict, replace
from datetime import timedelta
import math

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.swing.indicators import atr, ema
from app.swing.setups import evaluate, payoff, required_sessions
from tests.fixtures.swing_setups import setup_fixture


def run(fixture):
    cfg, cal, now, stock, bench, proof = fixture
    return evaluate("TEST1.NS", stock, bench, proof, cfg, cal, now)


def test_indicators_match_independent_references():
    # SYNTHETIC, deterministic, deliberately gapped closes test true-range logic.
    prices = 100 + np.sin(np.arange(450)) * 3 + np.arange(450) * .1
    np.testing.assert_allclose(ema(prices, 20), pd.Series(prices).ewm(span=20, adjust=False).mean(), rtol=1e-13)
    high, low = prices + 1, prices - 1
    ranges = [2] + [max(2, abs(high[i]-prices[i-1]), abs(low[i]-prices[i-1])) for i in range(1, len(prices))]
    actual = atr(high, low, prices, 14)
    expected = sum(ranges[:14]) / 14
    assert np.isnan(actual[:13]).all()
    assert actual[13] == pytest.approx(expected)
    # Closed-form weighted reference rather than mirroring the recurrence.
    for i in (14, 40, 449):
        decay = 13 / 14
        independent = expected * decay**(i-13) + math.fsum(ranges[j] / 14 * decay**(i-j) for j in range(14, i+1))
        assert actual[i] == pytest.approx(independent, rel=1e-12)


@pytest.mark.parametrize("kind,strategy", [("breakout", "DAILY_BREAKOUT_V1"), ("pullback", "DAILY_PULLBACK_V1")])
def test_two_setups_have_causal_plans_and_bounded_cost_aware_entries(kind, strategy):
    result = run(setup_fixture(kind))
    plan = next((p for p in result.plans if p.strategy == strategy), None)
    assert plan is not None, result.rejected
    assert plan.stop < plan.entry_min <= plan.entry_max < plan.target
    assert plan.entry_min <= plan.features.close <= plan.entry_max
    assert plan.first_entry_session.isoformat() == "2026-10-16"
    assert plan.expiry_session == plan.first_entry_session
    assert plan.gross_rr == pytest.approx(2)
    assert 1.5 <= plan.net_rr < plan.gross_rr
    assert payoff(plan.entry_max, plan.stop, plan.target, plan.costs)[1] == pytest.approx(1.5)
    assert plan.model_status == "RESEARCH_ONLY" and plan.lifecycle == "CANDIDATE"
    assert not plan.paper_eligible and plan.validation_record_id is None
    assert len(plan.id) == len(plan.data_hash) == len(plan.implementation_hash) == 64
    assert not plan.missing_gates
    with pytest.raises(Exception):
        plan.stop = 0


def test_pullback_does_not_need_legacy_spike_or_pe_gate():
    fixture = setup_fixture("pullback")
    cfg = fixture[0]
    cfg.MIN_VOLUME_RATIO = 9999
    cfg.MIN_RSI = 99
    cfg.MIN_PE = 999
    result = run(fixture)
    assert any(p.strategy == "DAILY_PULLBACK_V1" for p in result.plans), result.rejected


def test_future_suffix_and_active_bar_changes_cannot_change_plans():
    fixture = setup_fixture()
    cfg, cal, now, stock, bench, proof = fixture
    baseline = run(fixture)
    next_day = cal.calendar.next_session(pd.Timestamp(stock[-1].session)).date()
    future = stock[-1].model_copy(update={"session": next_day, "close": 100000, "high": 100001})
    future_bench = bench[-1].model_copy(update={"session": next_day, "close": 1})
    altered = evaluate("TEST1.NS", stock+(future,), bench+(future_bench,), proof, cfg, cal, now)
    assert altered == baseline
    active_now = cal.calendar.session_open(pd.Timestamp(next_day)).to_pydatetime() + timedelta(hours=1)
    # Same decision instant and available evidence, but arbitrary active bar.
    a = evaluate("TEST1.NS", stock, bench, proof, cfg, cal, active_now)
    b = evaluate("TEST1.NS", stock+(future,), bench+(future_bench,), proof, cfg, cal, active_now)
    assert a == b
    assert not b.plans and "entry_window_expired" in b.rejected[0].reasons


@pytest.mark.parametrize("mutation,reason", [
    ("gap", "insufficient_or_missing_exact_sessions"), ("short", "insufficient_or_missing_exact_sessions"),
    ("duplicate", "duplicate_or_unsorted_sessions"), ("unsorted", "duplicate_or_unsorted_sessions"),
    ("basis", "price_basis_unresolved_or_mixed"), ("identity", "unreconciled_identity_transition"),
    ("wrong_ticker", "instrument_or_ticker_mismatch"), ("benchmark", "insufficient_or_missing_exact_sessions")])
def test_data_problems_are_failures_without_fallback(mutation, reason):
    cfg, cal, now, stock, bench, proof = setup_fixture()
    if mutation == "gap": stock = stock[:200]+stock[201:]
    if mutation == "short": stock = stock[1:]
    if mutation == "duplicate": stock = stock[:200]+(stock[200],)+stock[200:]
    if mutation == "unsorted": stock = (stock[1], stock[0])+stock[2:]
    if mutation == "basis": stock = stock[:-1]+(stock[-1].model_copy(update={"price_basis": "unknown"}),)
    if mutation == "identity": stock = stock[:-1]+(stock[-1].model_copy(update={"isin": "NEW"}),)
    if mutation == "wrong_ticker": stock = stock[:-1]+(stock[-1].model_copy(update={"ticker": "TEST2.NS"}),)
    if mutation == "benchmark": bench = bench[:-1]
    result = evaluate("TEST1.NS", stock, bench, proof, cfg, cal, now)
    assert not result.plans and result.rejected[0].category == "failed"
    assert reason in result.rejected[0].reasons


def test_unavailable_proof_and_known_basis_do_not_imply_verified_entry():
    cfg, cal, now, stock, bench, proof = setup_fixture()
    result = evaluate("TEST1.NS", stock, bench, replace(proof, identity_verified=False, trading_basis_verified=False,
                                                      publication_verified=False, sector=None, sector_verified=False,
                                                      earnings_verified=False, surveillance_verified=False), cfg, cal, now)
    assert result.plans
    assert set(result.plans[0].missing_gates) == {"identity", "trading_basis", "publication", "sector", "earnings", "surveillance"}
    late = evaluate("TEST1.NS", stock, bench, replace(proof, available_at=now+timedelta(seconds=1)), cfg, cal, now)
    assert late.rejected[0].category == "failed"
    assert "source_not_available_at_decision" in late.rejected[0].reasons


def test_rules_filter_normally_and_parameters_change_plan_identity():
    fixture = setup_fixture()
    first = run(fixture).plans[0]
    cfg, cal, now, stock, bench, proof = fixture
    # Same stock, no volume confirmation: business rejection, never data failure.
    low = stock[:-1]+(stock[-1].model_copy(update={"volume": 100000}),)
    result = evaluate("TEST1.NS", low, bench, proof, cfg, cal, now)
    assert not result.plans and all(r.category == "filtered" for r in result.rejected)
    cfg.SWING_TARGET_R = 2.1
    second = run(fixture).plans[0]
    assert first.config_hash != second.config_hash and first.id != second.id
    assert asdict(first)["target"] != asdict(second)["target"]
    assert required_sessions(Settings(_env_file=None, SWING_MIN_HISTORY_SESSIONS=2)) == 400


def test_volume_boundary_excludes_current_bar_and_does_not_project():
    cfg, cal, now, stock, bench, proof = setup_fixture()
    exact = stock[:-1]+(stock[-1].model_copy(update={"volume": 1500000}),)
    plans = evaluate("TEST1.NS", exact, bench, proof, cfg, cal, now).plans
    breakout = next(p for p in plans if p.strategy == "DAILY_BREAKOUT_V1")
    assert breakout.features.average_volume == 1000000 and breakout.features.volume_ratio == 1.5
    below = stock[:-1]+(stock[-1].model_copy(update={"volume": 1499999}),)
    assert not any(p.strategy == "DAILY_BREAKOUT_V1" for p in evaluate("TEST1.NS", below, bench, proof, cfg, cal, now).plans)


def test_calendar_expiry_skips_holiday_and_weekend_and_limits_never_extend_target():
    cfg, cal, now, stock, bench, proof = setup_fixture(end_session="2026-10-01")
    cfg.SWING_ENTRY_VALID_SESSIONS = 2
    result = evaluate("TEST1.NS", stock, bench, proof, cfg, cal, now)
    assert result.plans
    assert result.plans[0].first_entry_session.isoformat() == "2026-10-05"
    assert result.plans[0].expiry_session.isoformat() == "2026-10-06"
    cfg.SWING_MIN_NET_RR = 10
    rejected = evaluate("TEST1.NS", stock, bench, proof, cfg, cal, now)
    assert not rejected.plans and any("net_reward_risk" in r.reasons for r in rejected.rejected)


@pytest.mark.parametrize("change", [
    {"SWING_CAPITAL_INR": 0}, {"SWING_RISK_FRACTION": float("nan")},
    {"SWING_MAX_GROSS_FRACTION": 1.1}, {"SWING_EMA_FAST": 60},
    {"SWING_RS_LOOKBACKS": "63,21"}, {"SWING_RS_LOOKBACKS": "21,21"},
    {"SWING_MIN_STOP_ATR": 4}, {"SWING_MODE": "paper"},
    {"SWING_MIN_CORRELATION_OBSERVATIONS": 80}])
def test_invalid_config_fails_fast(change):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **change)

"""SYNTHETIC risk/cash/portfolio boundary and deterministic-batch checks."""
from dataclasses import replace
from datetime import timedelta
import math

import numpy as np
import pytest
from pydantic import ValidationError

from app.swing.risk import Account, BookItem, ReturnPoint, allocate, correlation_ok
from app.swing.setups import evaluate, payoff
from tests.fixtures.swing_setups import other_plan, setup_fixture


def example(**changes):
    cfg, cal, now, stock, bench, proof = setup_fixture()
    for key, value in changes.items():
        setattr(cfg, key, value)
    plan = evaluate("TEST1.NS", stock, bench, proof, cfg, cal, now).plans[0]
    return cfg, cal, plan, Account(nav=1000000, cash=1000000, session=plan.session)


def histories(cal, session, *tickers):
    days = tuple(x.date() for x in cal.calendar.sessions if x.date() <= session)[-63:]
    # SYNTHETIC independent oscillators: pairwise correlations well below .75.
    data = {ticker: tuple(ReturnPoint(day, float(.01*np.sin(i*(j+1)+j))) for i, day in enumerate(days))
            for j, ticker in enumerate(tickers)}
    return days, data


def test_quantity_uses_costs_and_never_exceeds_independent_limits():
    c, cal, p, account = example()
    result = allocate((p,), c, account, {}, (), calendar=cal)[0]
    assert result.quantity > 0 and not result.reasons
    price = p.entry_max
    buy = price*(1+.0005)*(1+.001+.00015+.0001)
    stop_proceeds = p.stop*(1-.0005)*(1-.001-.0001)
    expected = math.floor(min(account.nav*.0025/(buy-stop_proceeds), account.cash/buy,
                              account.nav*.10/price, account.nav*.60/price,
                              account.nav*.015/(buy-stop_proceeds), account.nav*.25/price,
                              p.features.average_volume*.005))
    assert result.quantity == expected
    assert result.cash_required == pytest.approx(expected*buy)
    assert result.planned_stop_risk == pytest.approx(expected*(buy-stop_proceeds))
    assert result.estimated_entry_cost > 0 and result.status == "HYPOTHETICAL_ONLY"
    assert result.net_rr == pytest.approx(c.SWING_MIN_NET_RR)
    assert p.lifecycle == "CANDIDATE" and not p.paper_eligible


@pytest.mark.parametrize("key,value,binding", [
    ("SWING_RISK_FRACTION", .00001, "trade_risk"),
    ("SWING_MAX_POSITION_FRACTION", .0001, "position"),
    ("SWING_MAX_GROSS_FRACTION", .0001, "gross"),
    ("SWING_MAX_OPEN_RISK_FRACTION", .00001, "open_risk"),
    ("SWING_MAX_SECTOR_FRACTION", .0001, "sector"),
    ("SWING_MAX_ADV_PARTICIPATION", .000001, "liquidity")])
def test_each_cap_independently_limits_quantity(key, value, binding):
    c, cal, p, account = example(**{key: value})
    r = allocate((p,), c, account, {}, (), calendar=cal)[0]
    assert binding in r.limiting_constraints
    assert r.quantity <= 3


def test_cash_includes_fees_and_zero_shares_are_valid():
    c, cal, p, _ = example()
    held = BookItem(ticker="TESTHELD.NS", sector="SYNTHETIC-OTHER", quantity=9999,
                    mark=100, stop=100, session=p.session)
    book = Account(nav=1000000, cash=100, session=p.session, items=(held,))
    days, returns = histories(cal, p.session, p.ticker, held.ticker)
    r = allocate((p,), c, book, returns, days, calendar=cal)[0]
    assert r.quantity == 0 and "cash" in r.limiting_constraints
    assert r.reasons == ("zero_share_allocation",)


def test_missing_capital_or_book_never_invents_an_account():
    c, cal, p, _ = example(SWING_CAPITAL_INR=None)
    r = allocate((p,), c, None, {}, ())[0]
    assert r.quantity is None and r.cash_required is None
    assert "capital_not_configured" in r.reasons and "reconciled_account_required" in r.reasons
    c, cal, p, account = example()
    stale = account.model_copy(update={"session": p.session-timedelta(days=1)})
    assert "stale_account" in allocate((p,), c, stale, {}, ())[0].reasons
    changed = replace(p, missing_gates=("sector", "publication"))
    assert allocate((changed,), c, account, {}, ())[0].quantity == 0
    c.SWING_RISK_FRACTION *= 2
    assert "plan_configuration_changed" in allocate((p,), c, account, {}, ())[0].reasons


@pytest.mark.parametrize("value", [0, float("nan"), float("inf"), -1])
def test_invalid_open_never_allocates(value):
    c, cal, p, account = example()
    r = allocate((p,), c, account, {}, (), {p.ticker: value})[0]
    assert r.quantity == 0 and "entry_gap_outside_interval" in r.reasons


def test_both_gap_directions_rejected_and_actual_in_range_rr_rechecked():
    c, cal, p, account = example()
    for value in (p.entry_max+.01, p.entry_min-.01):
        r = allocate((p,), c, account, {}, (), {p.ticker: value})[0]
        assert r.quantity == 0 and "entry_gap_outside_interval" in r.reasons
    inside = allocate((p,), c, account, {}, (), {p.ticker: p.entry_min})[0]
    assert inside.quantity > 0 and inside.net_rr > c.SWING_MIN_NET_RR


def test_pending_reservations_heat_sector_cash_and_duplicate_dedupe():
    c, cal, p, account = example()
    pending = BookItem(ticker="TESTPENDING.NS", sector=p.evidence.sector, quantity=1000,
                       mark=140, stop=130, kind="pending", session=p.session,
                       reserved_cash_per_share=141, reserved_risk_per_share=16)
    book = account.model_copy(update={"items": (pending,)})
    days, returns = histories(cal, p.session, p.ticker, pending.ticker)
    before = book.model_dump()
    r = allocate((p,), c, book, returns, days, calendar=cal)[0]
    assert r.quantity == 0 and "open_risk" in r.limiting_constraints
    assert before == book.model_dump()  # no stop reset, cash release or mutation
    duplicate = pending.model_copy(update={"ticker": p.ticker})
    r = allocate((p,), c, account.model_copy(update={"items": (duplicate,)}), returns, days, calendar=cal)[0]
    assert "already_held_or_reserved" in r.reasons


def test_batch_order_is_deterministic_and_duplicate_ticker_allocates_once():
    c, cal, p, account = example()
    second = other_plan(p)
    days, returns = histories(cal, p.session, p.ticker, second.ticker)
    a = allocate((second, p), c, account, returns, days, calendar=cal)
    b = allocate((p, second), c, account, returns, days, calendar=cal)
    assert a == b and all(x.quantity > 0 for x in a)
    duplicate_strategy = replace(p, id="PULLBACK", strategy="DAILY_PULLBACK_V1")
    rows = allocate((duplicate_strategy, p), c, account, returns, days, calendar=cal)
    assert sum((r.quantity or 0) > 0 for r in rows) == 1
    assert any("already_held_or_reserved" in r.reasons for r in rows)


def test_correlation_known_unknown_constant_and_gaps_fail_closed():
    c, cal, p, account = example()
    days, data = histories(cal, p.session, "TEST1.NS", "TEST2.NS")
    a, b = data.values()
    assert correlation_ok(a, b, days, c) == (True, None)
    assert correlation_ok(a, a, days, c)[1] == "correlation_limit"
    assert correlation_ok(a, tuple(ReturnPoint(x.session, -x.value) for x in a), days, c)[1] == "correlation_limit"
    assert correlation_ok(a, None, days, c)[1] == "correlation_history_missing"
    assert correlation_ok(a, b[1:], days, c)[1] == "correlation_history_missing"
    assert correlation_ok(a, tuple(ReturnPoint(x.session, 0) for x in b), days, c)[1] == "correlation_values_invalid"
    assert correlation_ok(a, b[:20], days[:20], c)[1] == "correlation_history_missing"
    pending = BookItem(ticker="TEST2.NS", sector="SYNTHETIC-OTHER", quantity=1,
                       mark=100, stop=99, kind="pending", session=p.session,
                       reserved_cash_per_share=101, reserved_risk_per_share=2)
    book = account.model_copy(update={"items": (pending,)})
    assert "correlation_sessions_invalid" in allocate((p,), c, book, data, days[::2], calendar=cal)[0].reasons


def test_invalid_accounts_and_pending_unknown_costs_rejected():
    c, cal, p, account = example()
    with pytest.raises(ValidationError):
        Account(nav=1000000, cash=100, session=p.session)
    with pytest.raises(ValidationError):
        BookItem(ticker="TEST2.NS", sector="SYNTHETIC", quantity=1, mark=100, stop=90,
                 kind="pending", session=p.session)

"""SYNTHETIC daily lifecycle, cash/quantity conservation and adverse execution tests."""
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from app.swing.ledger import PaperConflict, PaperLedger, money
from app.swing.setups import digest
from tests.fixtures.swing_replay import advance, capture, replay_fixture


def next_day(fixture, day=None):
    return fixture[1].calendar.next_session(str(day or fixture[3].session)).date()


def test_funding_capture_reservation_restart_and_cash_flow_reconstruction(tmp_path):
    f = replay_fixture(tmp_path)
    cfg, cal, clock, p, ledger, market, engine = f
    receipt = capture(f)
    before = ledger.state("SYNTHETIC")
    assert before["orders"][p.id]["lifecycle"] == "ARMED"
    assert before["cash"] == Decimal("1000000") and before["reserved_cash"] > 0
    snapshot = PaperLedger(Path(cfg.SWING_PAPER_DB_PATH), clock).state("SYNTHETIC")
    assert snapshot == before
    # Exact retry returns prior receipt without evaluating the handler/version again.
    def forbidden(*args): raise AssertionError("Idempotent retry executed handler")
    assert ledger.command("SYNTHETIC", "capture", 1, {"operation": "capture", "plans": [p.id]}, forbidden) == receipt
    d = next_day(f)
    market.day(p, d)
    advance(f, d)
    state = ledger.state("SYNTHETIC")
    assert state["orders"][p.id]["lifecycle"] == "OPEN" and state["reserved_cash"] == 0
    events = ledger.events("SYNTHETIC", limit=1000)
    debits = sum((money(e["payload"]["cash_value"]) for e in events if e["kind"] == "BUY_FILLED"), Decimal(0))
    assert state["cash"] == Decimal("1000000")-debits
    position = state["positions"][p.ticker]
    assert position["quantity"] == next(e["payload"]["quantity"] for e in events if e["kind"] == "BUY_FILLED")
    assert state["nav"][-1]["benchmark_nav"] == "1010000.00"
    assert ledger.plan("SYNTHETIC", p.id) == p


@pytest.mark.parametrize("direction", ["up", "down"])
def test_entry_gaps_never_fill_on_daily_high_touch(tmp_path, direction):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    capture(f)
    day = next_day(f)
    price = p.entry_max+1 if direction == "up" else p.entry_min-1
    market.day(p, day, opening=price, high=max(price, p.target)+1, low=min(price, p.stop)-1)
    advance(f, day)
    s = ledger.state("SYNTHETIC")
    assert s["orders"][p.id]["lifecycle"] == "EXPIRED"
    assert not s["positions"] and s["cash"] == Decimal("1000000") and not s["reserved_cash"]


def test_same_day_stop_target_uses_stop_first_and_records_sensitivity(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    capture(f); day = next_day(f)
    market.day(p, day, low=p.stop-.1, high=p.target+.1)
    advance(f, day)
    s = ledger.state("SYNTHETIC")
    assert not s["positions"] and s["closed"][0]["exit_reason"] == "STOP"
    assert Decimal(s["closed"][0]["net_pnl"]) < 0
    ambiguity = [e["payload"] for e in ledger.events("SYNTHETIC") if e["kind"] == "AMBIGUOUS_EXIT"]
    assert len(ambiguity) == 1 and Decimal(ambiguity[0]["target_first_incremental_proceeds"]) > 0


def test_adverse_stop_gap_uses_actual_open_and_frozen_costs(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    capture(f); first = next_day(f); market.day(p, first); advance(f, first)
    quantity = ledger.state("SYNTHETIC")["positions"][p.ticker]["quantity"]
    second = next_day(f, first); price = p.stop-5
    market.day(p, second, opening=price)
    # New settings do not rewrite an existing trade's cash flows or stop.
    f[0].PERFORMANCE_SLIPPAGE_BPS = 900
    advance(f, second)
    sell = [e["payload"] for e in ledger.events("SYNTHETIC") if e["kind"] == "SELL_FILLED"][0]
    assert sell["raw_price"] == price and sell["multiplier"] == p.costs.sell_multiplier
    assert money(sell["cash_value"]) == money(Decimal(str(price))*Decimal(str(p.costs.sell_multiplier))*quantity)
    assert ledger.state("SYNTHETIC")["closed"][0]["exit_reason"] == "STOP_GAP"


def test_partial_entry_cancels_remainder_and_partial_exit_preserves_liability(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    capture(f); first = next_day(f); market.day(p, first, buy_capacity=3); advance(f, first)
    s = ledger.state("SYNTHETIC")
    assert s["positions"][p.ticker]["quantity"] == 3 and s["orders"][p.id]["cancelled_remainder"] > 0
    assert not s["reserved_cash"]
    second = next_day(f, first); market.day(p, second, low=p.stop-.1, sell_capacity=1); advance(f, second)
    pos = ledger.state("SYNTHETIC")["positions"][p.ticker]
    assert pos["quantity"] == 2 and pos["stop"] == p.stop and pos["exit_pending"] == "STOP"
    third = next_day(f, second); market.day(p, third, opening=p.features.close+1); advance(f, third)
    s = ledger.state("SYNTHETIC")
    assert not s["positions"] and s["orders"][p.id]["lifecycle"] == "CLOSED"
    events = ledger.events("SYNTHETIC", limit=1000)
    qty_bought = sum(e["payload"]["quantity"] for e in events if e["kind"] == "BUY_FILLED")
    qty_sold = sum(e["payload"]["quantity"] for e in events if e["kind"] == "SELL_FILLED")
    assert qty_bought == qty_sold == 3
    assert s["cash"] == Decimal("1000000")+Decimal(s["closed"][0]["net_pnl"])


def test_time_exit_is_next_open_after_holding_limit_not_known_close(tmp_path):
    f = replay_fixture(tmp_path, max_hold_sessions=1); p, ledger, market = f[3:6]
    capture(f); first = next_day(f); market.day(p, first); advance(f, first)
    assert ledger.state("SYNTHETIC")["positions"][p.ticker]["exit_pending"] == "TIME_EXIT"
    assert not ledger.state("SYNTHETIC")["closed"]
    second = next_day(f, first); market.day(p, second, opening=p.features.close+1); advance(f, second)
    assert ledger.state("SYNTHETIC")["closed"][0]["exit_session"] == str(second)
    assert ledger.state("SYNTHETIC")["closed"][0]["exit_reason"] == "TIME_EXIT"


@pytest.mark.parametrize("proof", [{"tradable": None}, {"buy_capacity": None}, {"actions_verified": False}])
def test_unknown_execution_context_retains_reservation_unresolved(tmp_path, proof):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    capture(f); first = next_day(f); market.day(p, first, **proof); advance(f, first)
    s = ledger.state("SYNTHETIC")
    assert s["orders"][p.id]["lifecycle"] == "UNRESOLVED"
    assert s["reserved_cash"] > 0 and s["cash"] == Decimal("1000000") and not s["positions"]
    second = next_day(f, first); market.day(p, second); advance(f, second)
    assert ledger.state("SYNTHETIC")["orders"][p.id]["lifecycle"] == "UNRESOLVED"


def test_locked_exit_stays_open_until_a_tradable_open(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    capture(f); first = next_day(f); market.day(p, first); advance(f, first)
    second = next_day(f, first); market.day(p, second, opening=p.stop-1, tradable=False); advance(f, second)
    assert p.ticker in ledger.state("SYNTHETIC")["positions"]
    third = next_day(f, second); market.day(p, third, opening=p.stop-2); advance(f, third)
    assert ledger.state("SYNTHETIC")["closed"][0]["exit_reason"] == "STOP_GAP"


def test_missing_bar_marks_nav_unavailable_and_never_resolves_as_a_winner(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    capture(f); first = next_day(f); market.day(p, first); advance(f, first)
    second = next_day(f, first); advance(f, second)
    s = ledger.state("SYNTHETIC")
    assert s["nav"][-1]["nav"] is None and s["nav"][-1]["stale_marks"] == [p.ticker]
    assert s["nav"][-1]["benchmark_nav"] is None
    third = next_day(f, second); market.day(p, third, opening=p.target+1); advance(f, third)
    assert ledger.state("SYNTHETIC")["closed"][0]["outcome_status"] == "UNRESOLVED_PATH"


def test_verified_split_adjusts_live_position_only_and_fractional_actions_block(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    capture(f); first = next_day(f); market.day(p, first); advance(f, first)
    before = ledger.state("SYNTHETIC")["positions"][p.ticker].copy()
    second = next_day(f, first); market.day(p, second, opening=before["mark"]/2,
                                          split_ratio="2", action_id="SYNTHETIC-SPLIT", previous_isin=p.isin)
    advance(f, second)
    after = ledger.state("SYNTHETIC")["positions"][p.ticker]
    assert after["quantity"] == before["quantity"]*2 and after["stop"] == before["stop"]/2
    assert after["entry_cash"] == before["entry_cash"] and ledger.plan("SYNTHETIC", p.id) == p
    third = next_day(f, second); market.day(p, third, opening=after["mark"]*.8,
                                          split_ratio="1.001", action_id="SYNTHETIC-FRACTIONAL", previous_isin=p.isin)
    advance(f, third)
    s = ledger.state("SYNTHETIC")
    assert s["positions"][p.ticker]["action_unresolved"] and s["nav"][-1]["nav"] is None


def test_failed_command_rolls_back_fills_and_retry_after_restart_is_once(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    capture(f); first = next_day(f); market.day(p, first)
    f[2].set_time(f[1].calendar.session_close(str(first)).to_pydatetime()+timedelta(minutes=10))
    before = ledger.state("SYNTHETIC")
    def fail(conn, state, emit):
        f[-1].advance(conn, state, emit, "SYNTHETIC", first)
        raise RuntimeError("SYNTHETIC crash after fill")
    with pytest.raises(RuntimeError):
        ledger.command("SYNTHETIC", "crash", before["version"], {"through": str(first)}, fail)
    assert PaperLedger(ledger.path, f[2]).state("SYNTHETIC") == before
    advance(f, first, "crash")
    assert len([e for e in ledger.events("SYNTHETIC") if e["kind"] == "BUY_FILLED"]) == 1


def test_late_capture_is_expired_not_retrospectively_filled(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    first = next_day(f); market.day(p, first)
    f[2].set_time(f[1].calendar.session_close(str(first)).to_pydatetime()+timedelta(minutes=10))
    capture(f); advance(f, first)
    s = ledger.state("SYNTHETIC")
    assert s["orders"][p.id]["lifecycle"] == "EXPIRED" and not s["positions"]


def test_plan_refresh_deduplication_revision_conflict_and_immutable_audit(tmp_path):
    f = replay_fixture(tmp_path); p, ledger = f[3:5]
    capture(f)
    refreshed = replace(p, decision_at=p.decision_at+timedelta(seconds=1))
    refreshed = replace(refreshed, id=digest(asdict(replace(refreshed, id=""))))
    f[2].sleep(1)
    capture(f, key="refresh", plans=(refreshed,))
    assert len(ledger.state("SYNTHETIC")["orders"]) == 1
    revision = replace(p, data_hash="SYNTHETIC-REVISED")
    revision = replace(revision, id=digest(asdict(replace(revision, id=""))))
    with pytest.raises(PaperConflict, match="revision"):
        capture(f, key="revised", plans=(revision,))
    with pytest.raises(sqlite3.IntegrityError):
        with ledger.connection(write=True) as conn:
            conn.execute("DELETE FROM events")
    backup = tmp_path/"SYNTHETIC-backup.db"
    ledger.backup(backup)
    assert PaperLedger(backup, f[2]).state("SYNTHETIC") == ledger.state("SYNTHETIC")


def test_future_range_and_version_conflicts_do_not_move_cash(tmp_path):
    f = replay_fixture(tmp_path); p, ledger = f[3:5]
    state = ledger.state("SYNTHETIC")
    with pytest.raises(PaperConflict, match="version"):
        ledger.command("SYNTHETIC", "wrong", state["version"]+1, {}, lambda *args: {})
    with pytest.raises(ValueError, match="completed"):
        ledger.command("SYNTHETIC", "future", state["version"], {},
                       lambda conn, s, emit: f[-1].advance(conn, s, emit, "SYNTHETIC", next_day(f)))
    assert ledger.state("SYNTHETIC") == state


def test_multiple_simultaneous_opens_use_known_new_position_marks_and_share_cash(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    second = replace(p, ticker="TEST2.NS", isin="SYNTHETIC-2", evidence=replace(p.evidence, sector="SYNTHETIC-OTHER"))
    second = replace(second, id=digest(asdict(replace(second, id=""))))
    capture(f, plans=(second, p))
    first = next_day(f); market.day(p, first); market.day(second, first)
    advance(f, first)
    s = ledger.state("SYNTHETIC")
    assert set(s["positions"]) == {p.ticker, second.ticker}
    assert s["reserved_cash"] == 0 and s["cash"] > 0
    assert not s["unresolved"]
    assert all(call[1] == p.session and call[3] == p.decision_at for call in market.return_calls)


def test_revision_of_processed_outcome_does_not_rewrite_fills_or_nav(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    capture(f); first = next_day(f); market.day(p, first); advance(f, first)
    before = ledger.state("SYNTHETIC")
    market.day(p, first, opening=p.stop-20, high=p.target+40, low=p.stop-30)
    advance(f, first, "revision")
    after = ledger.state("SYNTHETIC")
    assert before["cash"] == after["cash"] and before["positions"] == after["positions"]
    assert before["nav"] == after["nav"]


def test_benchmark_missing_initial_open_never_rebases_to_later_favorable_date(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    # No orders: isolate benchmark history behavior from entry evidence gates.
    first = next_day(f); advance(f, first)
    second = next_day(f, first); market.day(p, second); advance(f, second)
    records = ledger.state("SYNTHETIC")["nav"]
    assert all(r["benchmark_nav"] is None for r in records)
    assert records[-1]["benchmark_status"] == "benchmark_initial_open_unavailable"


def test_pending_split_stays_unresolved(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    capture(f); first = next_day(f); market.day(p, first, split_ratio="2", action_id="SYNTHETIC-SPLIT", previous_isin=p.isin)
    advance(f, first)
    state = ledger.state("SYNTHETIC")
    assert state["orders"][p.id]["lifecycle"] == "UNRESOLVED"
    assert state["nav"][-1]["nav"] is None


def test_observation_publication_after_replay_clock_cannot_fill(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    capture(f); first = next_day(f); market.day(p, first)
    old = market.observations[p.ticker, first]
    market.observations[p.ticker, first] = replace(old, available_at=old.available_at+timedelta(days=1))
    advance(f, first)
    state = ledger.state("SYNTHETIC")
    assert not state["positions"] and state["orders"][p.id]["lifecycle"] == "UNRESOLVED"


def test_intraday_target_proceeds_are_not_spent_at_the_same_open(tmp_path):
    f = replay_fixture(tmp_path); p, ledger, market = f[3:6]
    second = replace(p, ticker="TEST2.NS", isin="SYNTHETIC-2", evidence=replace(p.evidence, sector="SYNTHETIC-OTHER"))
    second = replace(second, id=digest(asdict(replace(second, id=""))))
    capture(f, plans=(p,second))
    reserved = {k:o["quantity"] for k,o in ledger.state("SYNTHETIC")["orders"].items() if o["lifecycle"]=="ARMED"}
    first = next_day(f); market.day(p, first, high=p.target+.1); market.day(second, first)
    advance(f, first)
    events = ledger.events("SYNTHETIC",limit=1000)
    buys = [e for e in events if e["kind"]=="BUY_FILLED"]
    sells = [e for e in events if e["kind"]=="SELL_FILLED"]
    assert len(buys)==2 and max(e["seq"] for e in buys)<min(e["seq"] for e in sells)
    assert all(e["payload"]["quantity"]<=reserved[e["payload"]["plan_id"]] for e in buys)


def test_session_bound_stops_unbounded_catchup_without_side_effect(tmp_path):
    f = replay_fixture(tmp_path); p,ledger,market = f[3:6]
    capture(f); first=next_day(f); second=next_day(f,first)
    before=ledger.state("SYNTHETIC")
    f[0].SWING_REPLAY_MAX_SESSIONS=1
    with pytest.raises(ValueError, match="bound"):
        advance(f,second)
    assert ledger.state("SYNTHETIC")==before

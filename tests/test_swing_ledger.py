"""SYNTHETIC ledger immutability, corruption, concurrent requests and recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import sqlite3

import pytest

from app.core.config import Settings
from app.swing.ledger import PaperConflict, PaperLedger
from app.swing.paper_service import PaperService
from tests.fixtures.swing_replay import capture, replay_fixture, advance


def test_same_command_concurrently_executes_once_and_different_payload_conflicts(tmp_path):
    f = replay_fixture(tmp_path); ledger = f[4]
    def submit():
        return ledger.command("SYNTHETIC", "concurrent", 1, {"value": 1},
                              lambda conn, state, emit: emit("REPLAY_RECORDED", {"SYNTHETIC": True}))
    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(lambda _: submit(), range(2)))
    assert a == b and ledger.state("SYNTHETIC")["version"] == 2
    with pytest.raises(PaperConflict, match="different content"):
        ledger.command("SYNTHETIC", "concurrent", 1, {"value": 2}, lambda *args: {})


def test_durable_jobs_are_found_after_restart_and_failure_does_not_erase_queue(tmp_path):
    f = replay_fixture(tmp_path); cfg, cal, clock, p, ledger, market, engine = f
    request = {"vintage": "SYNTHETIC", "through": str(p.session), "expected_version": 1}
    job_id = ledger.enqueue("SYNTHETIC", "job", request)
    restarted = PaperLedger(ledger.path, clock)
    assert restarted.next_job()["id"] == job_id
    assert restarted.enqueue("SYNTHETIC", "job", request) == job_id
    restarted.record_job_error(job_id, "SYNTHETIC_FAILURE")
    assert restarted.job(job_id)["status"] == "FAILED" and restarted.next_job() is None
    assert restarted.state("SYNTHETIC")["version"] == 1
    second = restarted.enqueue("SYNTHETIC", "retry-new-key", request)
    assert restarted.next_job()["id"] == second


def test_hash_chain_detects_tampering_even_if_sql_trigger_removed(tmp_path):
    f = replay_fixture(tmp_path); ledger = f[4]
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("DROP TRIGGER immutable_events_UPDATE")
        conn.execute("UPDATE events SET payload='{}' WHERE seq=1")
    with pytest.raises(PaperConflict, match="hash chain"):
        ledger.state("SYNTHETIC")


def test_refuses_unrelated_future_or_ambiguous_schema_without_migrating(tmp_path):
    f = replay_fixture(tmp_path); ledger = f[4]
    other = tmp_path/"SYNTHETIC-other.db"
    with sqlite3.connect(other) as conn:
        conn.execute("CREATE TABLE private_data(value)")
    with pytest.raises(PaperConflict, match="refusing migration"):
        PaperLedger(other, f[2]).create_account("TEST", 100, date(2026,10,15), "fund")
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("INSERT INTO paper_schema VALUES(2)")
    with pytest.raises(PaperConflict, match="schema"):
        ledger.state("SYNTHETIC")


@pytest.mark.parametrize("capital", [0, -1, "NaN", "Infinity", "1.001"])
def test_no_invalid_or_implicit_funding(tmp_path, capital):
    f = replay_fixture(tmp_path)
    with pytest.raises(ValueError):
        f[4].create_account("OTHER", capital, f[3].session, "fund")


def test_cancel_releases_only_known_unfilled_reservation_and_is_idempotent(tmp_path):
    f = replay_fixture(tmp_path); cfg, cal, clock, p, ledger, market, engine = f
    capture(f)
    service = PaperService(cfg, clock, cal)
    state = ledger.state("SYNTHETIC")
    result = service.cancel("SYNTHETIC", p.id, "cancel", state["version"], "SYNTHETIC user cancellation")
    assert service.cancel("SYNTHETIC", p.id, "cancel", state["version"], "SYNTHETIC user cancellation") == result
    assert ledger.state("SYNTHETIC")["reserved_cash"] == 0
    assert ledger.state("SYNTHETIC")["orders"][p.id]["lifecycle"] == "CANCELLED"


def test_missing_store_read_does_not_initialize_it_and_bad_paths_fail(tmp_path):
    f = replay_fixture(tmp_path)
    absent = tmp_path/"absent.db"
    with pytest.raises(FileNotFoundError):
        PaperLedger(absent, f[2]).state("SYNTHETIC")
    assert not absent.exists()
    for changes in ({"SWING_PAPER_DB_PATH": "data/swing.db"}, {"SWING_PAPER_DB_PATH": "data/history.db"},
                    {"SWING_REPLAY_MAX_SESSIONS": 0}, {"SWING_PAPER_DB_PATH": " "}):
        with pytest.raises(ValueError): Settings(_env_file=None, **changes)


def test_command_cannot_steal_an_accepted_job_idempotency_key(tmp_path):
    f = replay_fixture(tmp_path); ledger = f[4]
    request = {"vintage":"SYNTHETIC", "through":str(f[3].session), "expected_version":1}
    job_id = ledger.enqueue("SYNTHETIC", "queued", request)
    with pytest.raises(PaperConflict, match="different accepted job"):
        ledger.command("SYNTHETIC", "queued", 1, {"operation":"CANCEL"}, lambda *args: {})
    assert ledger.job(job_id)["status"] == "QUEUED"
    assert ledger.state("SYNTHETIC")["version"] == 1


def test_cancellation_cannot_release_an_unknown_entry_liability(tmp_path):
    f = replay_fixture(tmp_path); cfg,cal,clock,p,ledger,market,engine=f
    capture(f)
    first=cal.calendar.next_session(str(p.session)).date()
    market.day(p,first,buy_capacity=None)
    advance(f,first)
    state=ledger.state("SYNTHETIC")
    with pytest.raises(PaperConflict, match="liability"):
        PaperService(cfg,clock,cal).cancel("SYNTHETIC",p.id,"cancel",state["version"],"SYNTHETIC")
    assert ledger.state("SYNTHETIC")==state and state["reserved_cash"]>0


def test_oversubscribed_cash_event_rolls_back_atomically(tmp_path):
    f=replay_fixture(tmp_path); ledger=f[4];p=f[3]
    capture(f);before=ledger.state("SYNTHETIC")
    def apply(conn,state,emit):
        emit("ORDER_RESERVED", {**state["orders"][p.id],"reserved_cash":"1000000.01"})
    with pytest.raises(PaperConflict,match="Reservations exceed"):
        ledger.command("SYNTHETIC","oversubscribe",before["version"],{},apply)
    assert ledger.state("SYNTHETIC")==before

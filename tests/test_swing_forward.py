"""SYNTHETIC prospective evidence tests. No live data, network or real account."""
from copy import deepcopy
from dataclasses import replace
from dataclasses import asdict
from datetime import timedelta
import json
import sqlite3

import pytest

from app.swing.forward import ForwardService, reconcile
from app.swing.forward_store import ForwardJournal, ForwardSettings
from app.swing.ledger import PaperConflict
from app.swing.ledger import encoded
from tests.fixtures.swing_forward import forward_fixture, paper_day


@pytest.fixture
def env(tmp_path, monkeypatch):
    return forward_fixture(tmp_path, monkeypatch)


def test_minimum_window_and_store_isolation(tmp_path):
    with pytest.raises(ValueError):
        ForwardSettings(_env_file=None, SWING_FORWARD_MIN_SESSIONS=29)
    with pytest.raises(ValueError):
        ForwardJournal(ForwardSettings(_env_file=None, SWING_FORWARD_DB_PATH=" "), None)
    target = tmp_path/"source.db"
    target.write_bytes(b"SYNTHETIC")
    with pytest.raises(ValueError):
        ForwardJournal(ForwardSettings(_env_file=None, SWING_FORWARD_DB_PATH=str(target)), None, (target,))


def test_missing_read_does_not_initialize_journal(tmp_path):
    journal = ForwardJournal(ForwardSettings(_env_file=None, SWING_FORWARD_DB_PATH=str(tmp_path/"forward.db")), None)
    assert journal.campaigns() == [] and not journal.path.exists()


@pytest.mark.parametrize("ddl", ["CREATE TABLE unrelated(x)", "CREATE TABLE forward_schema(version); INSERT INTO forward_schema VALUES(99)"])
def test_unrelated_or_future_schema_refused(tmp_path, ddl):
    path = tmp_path/"unknown.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(ddl)
    journal = ForwardJournal(ForwardSettings(_env_file=None, SWING_FORWARD_DB_PATH=str(path)), None)
    with pytest.raises(PaperConflict):
        journal.campaigns()


def test_registration_retry_and_account_unique(env):
    service, clock, cal, plan, sessions, engine, original = env
    clock.set_time(cal.calendar.session_close(str(sessions[1])).to_pydatetime())
    assert service.register("SYNTHETIC-campaign", "SYNTHETIC", sessions[0], sessions[-1],
        "SYNTHETIC predeclared breakout hypothesis", "register") == original
    with pytest.raises(PaperConflict):
        service.register("SYNTHETIC-campaign", "SYNTHETIC", sessions[0], sessions[-1],
            "SYNTHETIC changed hypothesis for same command", "register")


def test_registration_cannot_backdate_or_shorten_window(env):
    service, clock, cal, plan, sessions, *_ = env
    with pytest.raises(ValueError, match="minimum"):
        service.register("short", "SYNTHETIC", sessions[0], sessions[2], "SYNTHETIC sufficiently long hypothesis", "short")
    clock.set_time(cal.calendar.session_open(str(sessions[0])).to_pydatetime())
    with pytest.raises(ValueError, match="future"):
        service.register("late", "SYNTHETIC", sessions[0], sessions[-1], "SYNTHETIC sufficiently long hypothesis", "late")


def test_capture_freezes_empty_signal_day_and_is_idempotent(env):
    service, clock, cal, plan, sessions, *_ = env
    paper_day(env, sessions[0])
    before = service.paper.ledger.path.read_bytes()
    result = service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "capture")
    assert service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "capture") == result
    assert service.paper.ledger.path.read_bytes() == before
    record = service.journal.read("SYNTHETIC-campaign")["days"][0]["payload"]
    assert not record["new_plans"] and record["session"] == str(sessions[0])
    assert "independent_state_reconstruction_mismatch" not in record["failures"]
    report = service.report("SYNTHETIC-campaign")
    assert report["captured_sessions"] == 1 and report["reconciled_sessions"] == 0
    assert not report["promotion_allowed"] and not report["paper_eligible"]
    assert service.campaigns() == []  # SYNTHETIC never appears in live discovery


def test_forecast_matches_next_observed_open_and_simulated_fill(env):
    service, clock, cal, plan, sessions, *_ = env
    paper_day(env, sessions[0], capture_plan=True)
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "first")
    paper_day(env, sessions[1])
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "second")
    record = service.journal.read("SYNTHETIC-campaign")["days"][-1]["payload"]
    row = record["comparisons"][0]
    assert row["simulated_fill_quantity"] > 0 and row["status"] == "REFERENCE_CHECKED" and row["open_in_range"]
    assert not any(f in record["failures"] for f in ("fill_reference_mismatch", "fill_cash_or_cost_mismatch", "fill_exceeds_prior_reservation", "independent_state_reconstruction_mismatch"))


def test_after_next_open_cannot_backfill_capture(env):
    service, clock, cal, plan, sessions, *_ = env
    paper_day(env, sessions[0])
    clock.set_time(cal.calendar.session_open(str(sessions[1])).to_pydatetime())
    with pytest.raises(ValueError, match="next open"):
        service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "late")
    assert service.report("SYNTHETIC-campaign")["missing_sessions"] == [str(sessions[0])]


def test_sealed_session_refuses_data_revision(env):
    service, _, _, _, sessions, *_ = env
    paper_day(env, sessions[0])
    first = service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "first")
    with pytest.raises(PaperConflict):
        service.capture("SYNTHETIC-campaign", "SYNTHETIC-REVISED", "first")
    with pytest.raises(PaperConflict, match="sealed"):
        service.capture("SYNTHETIC-campaign", "SYNTHETIC-REVISED", "different-key")
    assert service.journal.read("SYNTHETIC-campaign")["head"] == first["hash"]


def test_unknown_capacity_is_unresolved_not_successful_fill(env):
    service, _, _, _, sessions, *_ = env
    paper_day(env, sessions[0], capture_plan=True)
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "first")
    paper_day(env, sessions[1], buy_capacity=None)
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "second")
    record = service.journal.read("SYNTHETIC-campaign")["days"][-1]["payload"]
    assert record["comparisons"][0]["status"] == "UNRESOLVED"
    assert record["comparisons"][0]["simulated_fill_quantity"] == 0
    assert "unresolved_paper_liabilities" in record["failures"]


def test_gap_nonfill_is_not_data_failure(env):
    service, _, _, plan, sessions, *_ = env
    paper_day(env, sessions[0], capture_plan=True)
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "first")
    paper_day(env, sessions[1], opening=plan.entry_max*1.1)
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "second")
    row = service.report("SYNTHETIC-campaign")["days"][-1]["comparisons"][0]
    assert row["status"] == "NO_SIMULATED_FILL" and not row["open_in_range"]
    assert row["dispositions"] == ["entry_gap_or_not_tradable"]


@pytest.mark.parametrize("change,expected", [
    ("raw_price", "fill_reference_mismatch"), ("cash_value", "fill_cash_or_cost_mismatch")])
def test_reconciliation_detects_fill_reference_or_cash_mismatch(env, change, expected):
    service, clock, cal, plan, sessions, *_ = env
    paper_day(env, sessions[0], capture_plan=True)
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "first")
    previous = service.journal.read("SYNTHETIC-campaign")["days"][0]["payload"]
    paper_day(env, sessions[1])
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "second")
    current = service.journal.read("SYNTHETIC-campaign")["days"][1]["payload"]
    events = deepcopy(current["events"])
    fill = next(e for e in events if e["kind"] == "BUY_FILLED")
    fill["payload"][change] = float(fill["payload"][change])+1
    registration = service.journal.read("SYNTHETIC-campaign")["registration"]
    _, failures, _ = reconcile(registration, previous["state"], previous["new_plans"], events, current["observations"], str(sessions[1]), clock.now(), cal)
    assert expected in failures


def test_revised_observation_is_flagged_without_rewriting_ledger(env):
    service, _, _, plan, sessions, *_ = env
    paper_day(env, sessions[0], capture_plan=True)
    # Change the observed benchmark after S3 committed its original bar.
    market = service.paper.market
    obs = market.observations["^CRSLDX", sessions[0]]
    market.observations["^CRSLDX", sessions[0]] = replace(obs, bar=obs.bar.model_copy(update={"close": 101.5}))
    before = service.paper.ledger.path.read_bytes()
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "first")
    report = service.report("SYNTHETIC-campaign")
    assert "observed_data_revision_requires_review" in report["failures"]
    assert service.paper.ledger.path.read_bytes() == before


def test_missed_day_and_unreconciled_new_events_stay_visible(env):
    service, _, _, _, sessions, *_ = env
    paper_day(env, sessions[0])
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "first")
    paper_day(env, sessions[1])
    assert "unreconciled_ledger_events" in service.report("SYNTHETIC-campaign")["failures"]
    paper_day(env, sessions[2])
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "third")
    result = service.report("SYNTHETIC-campaign")
    assert str(sessions[1]) in result["missing_sessions"]
    assert "late_or_backfilled_paper_events" in result["failures"]


def test_store_is_append_only_and_tamper_detected(env):
    service, _, _, _, sessions, *_ = env
    paper_day(env, sessions[0])
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "first")
    path = service.journal.path
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM days")
        conn.execute("DROP TRIGGER immutable_days_UPDATE")
        conn.execute("UPDATE days SET payload='{}'")
    with pytest.raises(PaperConflict, match="chain"):
        service.journal.read("SYNTHETIC-campaign")


def test_code_drift_cannot_promote_even_with_a_capture(env, monkeypatch):
    service, _, _, _, sessions, *_ = env
    paper_day(env, sessions[0])
    monkeypatch.setattr("app.swing.forward.fingerprint", lambda: {"SYNTHETIC": "changed"})
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "first")
    result = service.report("SYNTHETIC-campaign")
    assert "frozen_implementation_or_configuration_drift" in result["failures"]
    assert result["model_status"] == "RESEARCH_ONLY" and not result["promotion_allowed"]


def test_thirty_synthetic_sessions_never_qualify_as_real_validation(env):
    service, _, _, _, sessions, *_ = env
    for session in sessions:
        paper_day(env, session)
        service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", str(session))
    result = service.report("SYNTHETIC-campaign")
    assert result["captured_sessions"] == 30 and result["missing_sessions"] == []
    assert "minimum_prospective_window_incomplete" not in result["failures"]
    assert "synthetic_evidence" in result["failures"]
    assert not result["operational_gate_passed"] and not result["promotion_allowed"]
    assert service.campaigns() == []


def test_report_detects_configuration_drift_without_more_captures(env):
    service = env[0]
    service.config.SWING_TARGET_R += 1
    assert "frozen_implementation_or_runtime_drift" in service.report("SYNTHETIC-campaign")["failures"]


def test_wind_down_forbids_new_plans_but_does_not_rewrite_them(env):
    service, clock, cal, plan, sessions, *_ = env
    registration = service.journal.read("SYNTHETIC-campaign")["registration"]
    session = sessions[-1]
    now = cal.calendar.session_close(str(session)).to_pydatetime()+timedelta(minutes=30)
    late = replace(plan, session=session, decision_at=now,
                   first_entry_session=cal.calendar.next_session(str(session)).date())
    payload = json.loads(encoded(asdict(late)))
    event = {"kind": "PLAN_CAPTURED", "created_at": now.isoformat(),
             "payload": {"plan_id": late.id, "ticker": late.ticker, "session": str(session)}}
    _, failures, _ = reconcile(registration, registration["seed_state"], {late.id: payload},
        [event], {}, str(session), now, cal)
    assert "new_plan_during_declared_wind_down" in failures
    assert payload == json.loads(encoded(asdict(late)))


def test_synthetic_campaign_is_hidden_from_direct_http_reads(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.swing.forward_api import router
    app = FastAPI()
    app.state.forward = env[0]
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/swing/forward/campaigns").json() == []
        assert client.get("/api/swing/forward/campaigns/SYNTHETIC-campaign").status_code == 404


def test_reconciliation_checks_initial_benchmark_open_independently(env):
    service, clock, cal, plan, sessions, *_ = env
    paper_day(env, sessions[0])
    service.capture("SYNTHETIC-campaign", "SYNTHETIC-VINTAGE", "first")
    material = service.journal.read("SYNTHETIC-campaign")
    record = material["days"][0]["payload"]
    events = deepcopy(record["events"])
    mark = next(e["payload"] for e in events if e["kind"] == "SESSION_MARKED")
    mark["benchmark_base"] = 200
    _, failures, _ = reconcile(material["registration"], material["registration"]["seed_state"],
        {}, events, record["observations"], str(sessions[0]), clock.now(), cal)
    assert "benchmark_open_base_mismatch" in failures

"""S4 frozen design, uncertainty, integrity and holdout regression tests; SYNTHETIC only."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest
from pydantic import ValidationError

from app.swing.ledger import PaperLedger
from app.swing.setups import digest
from app.swing.validation import ValidationService, extract_panel
from app.swing.validation_protocol import Gates, Study, ValidationSettings, Variant, schedule
from app.swing.validation_stats import baseline_curve, clustered_interval, drawdown, metric_failures, summarize
from app.swing.validation_store import ValidationRegistry
from tests.fixtures.swing_validation import metric_panel, study_fixture
from tests.fixtures.swing_replay import replay_fixture, capture, advance


@pytest.fixture
def registered(tmp_path):
    cfg, cal, clock, study = study_fixture()
    cfg.SWING_PAPER_DB_PATH = str(tmp_path/"SYNTHETIC-paper.db")
    cfg.SWING_DB_PATH = str(tmp_path/"SYNTHETIC-data.db")
    settings = ValidationSettings(_env_file=None, VALIDATION_DB_PATH=str(tmp_path/"validation.db"),
                                  VALIDATION_SIGNING_KEY="SYNTHETIC-dedicated-signing-key-123456789")
    registry = ValidationRegistry(settings, clock)
    record = registry.register(study, cal)
    return cfg, cal, clock, study, registry, record


def test_registration_freezes_hashes_calendar_signature_and_retries(registered):
    cfg, cal, clock, study, registry, record = registered
    assert record["signature"] and registry.verify(record)["code_hashes"]
    assert registry.register(study, cal) == record
    s = record["payload"]["schedule"]
    assert s["horizon"] == 12 and s["block_sessions"] == 20
    assert s["windows"]["test1"]["signal_end"] < str(study.folds[0].test.end)
    with pytest.raises(ValueError, match="different protocol"):
        registry.register(study.model_copy(update={"hypothesis": "A different frozen hypothesis for testing"}), cal)
    with registry.connection(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM studies")


def test_holdout_cannot_be_historical_or_reused(registered):
    cfg, cal, clock, study, registry, _ = registered
    with pytest.raises(ValueError, match="reuse forbidden"):
        registry.register(study.model_copy(update={"id": "other-study"}), cal)
    clock.set_time(cal.calendar.session_open(str(study.holdout.start)).to_pydatetime())
    other_registry = ValidationRegistry(ValidationSettings(_env_file=None, VALIDATION_DB_PATH=str(registry.path.parent/"other.db")), clock)
    with pytest.raises(ValueError, match="strictly after"):
        other_registry.register(study, cal)


def test_holdout_claim_is_one_shot_even_after_crash(registered):
    _, cal, clock, study, registry, _ = registered
    with pytest.raises(ValueError, match="must complete"):
        registry.claim_holdout(study.id, cal)
    clock.set_time(cal.calendar.session_close(str(study.holdout.end)).to_pydatetime()+timedelta(seconds=1))
    registry.claim_holdout(study.id, cal)
    with pytest.raises(ValueError, match="consumed"):
        registry.claim_holdout(study.id, cal)


@pytest.mark.parametrize("change", ["same_account", "unknown_variant", "unknown_field", "missing_config", "neighbor_without_parameter"])
def test_protocol_rejects_search_and_account_ambiguity(change):
    _, _, _, study = study_fixture()
    material = study.model_dump(mode="json")
    if change == "same_account":
        material["variants"][0]["accounts"]["test2"] = material["variants"][0]["accounts"]["test1"]
    elif change == "unknown_variant":
        material["search_budget"] += 1
    elif change == "unknown_field":
        material["optimized_on_holdout"] = True
    elif change == "missing_config":
        material["variants"][0]["configuration"].pop("SWING_TARGET_R")
    else:
        variant = deepcopy(material["variants"][0])
        variant.update(name="neighbor", role="parameter")
        variant["accounts"] = {k: v+"-neighbor" for k, v in variant["accounts"].items()}
        material["variants"].append(variant)
        material["search_budget"] = 2
    with pytest.raises((ValidationError, ValueError)):
        Study.model_validate(material)


@pytest.mark.parametrize("change", ["overlap", "embargo", "training", "short", "weekend"])
def test_schedule_rejects_leakage_and_short_windows(change):
    _, cal, _, study = study_fixture()
    material = study.model_dump(mode="json")
    if change == "overlap":
        material["folds"][1]["test"] = {**material["folds"][0]["test"], "id": "test2"}
    elif change == "embargo":
        material["folds"][0]["validation"]["start"] = material["folds"][0]["train"]["end"]
    elif change == "training":
        material["folds"][1]["train"]["end"] = material["folds"][0]["train"]["end"]
    elif change == "short":
        material["holdout"]["end"] = material["holdout"]["start"]
    else:
        material["holdout"]["start"] = "2026-10-17"
    with pytest.raises(ValueError):
        schedule(Study.model_validate(material), cal)


def test_manifest_tamper_key_rotation_and_unsigned_are_explicit(registered):
    _, _, _, _, registry, record = registered
    tampered = deepcopy(record)
    tampered["payload"]["protocol"]["capital_inr"] *= 2
    with pytest.raises(ValueError, match="hash mismatch"):
        registry.verify(tampered)
    tampered["sha256"] = digest(tampered["payload"])
    with pytest.raises(ValueError, match="authentication failed"):
        registry.verify(tampered)
    settings = ValidationSettings(_env_file=None, VALIDATION_DB_PATH=str(registry.path))
    unsigned = ValidationRegistry(settings, registry.clock)
    with pytest.raises(ValueError, match="authentication failed"):
        unsigned.study("SYNTHETIC-study")
    assert unsigned.envelope({"value": 1})["authentication"] == "UNSIGNED"
    with pytest.raises(ValidationError):
        ValidationSettings(_env_file=None, VALIDATION_SIGNING_KEY="short")


def test_registry_refuses_other_schema_and_read_does_not_initialize(tmp_path):
    path = tmp_path/"unknown.db"
    registry = ValidationRegistry(ValidationSettings(_env_file=None, VALIDATION_DB_PATH=str(path)), study_fixture()[2])
    with pytest.raises(FileNotFoundError):
        registry.study("missing")
    assert not path.exists()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unrelated(value)")
    with pytest.raises(ValueError, match="refusing migration"):
        registry.register(study_fixture()[3], study_fixture()[1])


def test_validation_cannot_use_source_or_ledger_path(registered):
    cfg, cal, clock, _, registry, _ = registered
    registry.path = Path(cfg.SWING_PAPER_DB_PATH)
    with pytest.raises(ValueError, match="separate"):
        ValidationService(registry, cfg, clock, cal)


def test_development_is_reproducible_and_never_reads_holdout(registered):
    cfg, cal, clock, study, registry, _ = registered
    clock.set_time(cal.calendar.session_close(str(study.folds[-1].test.end)).to_pydatetime()+timedelta(seconds=1))
    service = ValidationService(registry, cfg, clock, cal)
    first = service.evaluate(study.id)
    assert first == service.evaluate(study.id)
    assert "terminal" not in json.dumps(first["payload"]["windows"])
    assert first["payload"]["model_status"] == "RESEARCH_ONLY"
    assert "ledger_unavailable:FileNotFoundError" in first["payload"]["failures"]
    assert not Path(cfg.SWING_PAPER_DB_PATH).exists()
    assert registry.report(first["sha256"]) == first


def test_terminal_consumption_is_persistent_and_completed_report_idempotent(registered):
    cfg, cal, clock, study, registry, _ = registered
    clock.set_time(cal.calendar.session_close(str(study.holdout.end)).to_pydatetime()+timedelta(seconds=1))
    service = ValidationService(registry, cfg, clock, cal)
    first = service.evaluate(study.id, holdout=True)
    assert service.evaluate(study.id, holdout=True) == first
    assert "out_of_sample_development_gates_not_passed" in first["payload"]["failures"]
    with pytest.raises(ValueError, match="consumed"):
        registry.claim_holdout(study.id, cal)


def test_code_changes_cannot_retune_registered_study(registered, monkeypatch):
    cfg, cal, clock, study, registry, _ = registered
    monkeypatch.setattr("app.swing.validation.code_hashes", lambda: {"changed": "implementation"})
    with pytest.raises(ValueError, match="Code/calendar changed"):
        ValidationService(registry, cfg, clock, cal).evaluate(study.id)


def test_block_uncertainty_keeps_correlated_dates_together_and_is_seeded():
    gates = Gates(bootstrap_replicates=1000)
    panel = metric_panel()
    block = clustered_interval([panel], 20, gates)
    independent = clustered_interval([panel], 1, gates)
    assert block == clustered_interval([panel], 20, gates)
    assert block["nonoverlapping_periods"] == 6
    assert block["expectancy_inr"][1]-block["expectancy_inr"][0] > 2*(independent["expectancy_inr"][1]-independent["expectancy_inr"][0])


def test_flat_days_and_sparse_bootstrap_never_disappear():
    gates = Gates(bootstrap_replicates=1000)
    panel = metric_panel()
    panel["trades"] = panel["trades"][:1]
    bounds = clustered_interval([panel], 20, gates)
    assert bounds["signal_calendar_sessions"] == 120 and bounds["empty_replicates"] > 0
    metrics = summarize([panel], gates, 20)
    assert "sparse_bootstrap" in metric_failures(metrics, gates)


def test_metrics_chain_only_disjoint_test_windows_and_costs_reduce_expectancy():
    gates = Gates(bootstrap_replicates=100)
    panel = metric_panel(clustered=False)
    metrics = summarize([panel, panel], gates, 20)
    assert metrics["trades"] == 1200
    assert metrics["net_return"] == pytest.approx((1+.0005*119)**2-1)
    assert metrics["mean_net_pnl"] == 100
    assert metrics["mean_net_r"] == 1
    assert metrics["cost_stress"][0]["mean_net_pnl"] < metrics["mean_net_pnl"]
    assert metrics["max_drawdown"] == 0


def test_drawdown_includes_initial_capital_and_underwater_duration():
    assert drawdown([100, 90, 80, 100, 110, 99]) == pytest.approx((.2, 2))


def test_constant_baseline_cash_index_and_transaction_costs():
    panel = metric_panel(n=3, clustered=False)
    for i, row in enumerate(panel["daily"]):
        row["benchmark_nav"] = 1000000*(1.1**(i+1))
    curve = baseline_curve(panel, .6)
    assert curve[-1] == pytest.approx(1000000*1.06**3)
    panel["buy_multiplier"], panel["sell_multiplier"] = 1.001, .999
    assert baseline_curve(panel, .6)[-1] < curve[-1]
    assert baseline_curve(panel, 0)[-1] == 1000000


@pytest.mark.parametrize("field,value", [("alpha", 0), ("extra_cost_bps", (10, float("nan"))), ("max_drawdown", float("inf")), ("bootstrap_replicates", 99)])
def test_statistical_bounds_are_finite_and_validated(field, value):
    with pytest.raises(ValidationError):
        Gates(**{field: value})


def test_s3_actual_fill_ledger_is_reconciled_and_synthetic_cannot_promote(tmp_path):
    fixture = replay_fixture(tmp_path)
    cfg, cal, clock, plan, ledger, market, engine = fixture
    capture(fixture)
    day = plan.first_entry_session
    market.day(plan, day, high=plan.target+1)
    advance(fixture, day)
    _, _, _, study = study_fixture()
    variant = study.variants[0]
    from app.swing.validation_protocol import Window
    from app.swing.setups import implementation_hash
    window = Window(id="isolated", start=plan.session, end=day)
    with ledger.connection() as conn:
        panel = extract_panel(ledger, conn, "SYNTHETIC", variant, window,
            {"seed": str(plan.session), "sessions": [str(day)], "signal_end": str(plan.session)},
            study, {"setup_implementation": implementation_hash()}, cal, clock.now())
    assert "synthetic_evidence" in panel["failures"]
    assert len(panel["trades"]) == 1 and panel["trades"][0]["pnl"] > 0
    assert panel["trades"][0]["buy_gross"] > 0 and panel["trades"][0]["sell_gross"] > 0
    assert panel["manifest"]["event_head"]
    assert panel["entry_exposure"] > 0 and panel["daily"][-1]["exposure"] == 0


@pytest.mark.parametrize("scenario", ["delay", "gap", "halt"])
def test_adverse_execution_replay_uses_isolated_book_and_verified_later_data(tmp_path, scenario):
    from app.swing.validation_stress import stress_replay
    from app.swing.validation_protocol import Window
    from app.swing.setups import implementation_hash
    fixture = replay_fixture(tmp_path)
    cfg, cal, clock, plan, ledger, market, engine = fixture
    capture(fixture)
    first = plan.first_entry_session
    second = cal.calendar.next_session(str(first)).date()
    market.day(plan, first, high=plan.target+1)
    advance(fixture, first)
    market.day(plan, second, opening=plan.target+2)
    advance(fixture, second)
    before = ledger.path.read_bytes()
    _, _, _, study = study_fixture()
    gates = Gates(bootstrap_replicates=100, execution_stress_fraction=1)
    variant = study.variants[0].model_copy(update={"configuration": dict(plan.configuration)})
    window = Window(id="isolated", start=plan.session, end=second)
    dates = {"seed": str(plan.session), "sessions": [str(first), str(second)], "signal_end": str(plan.session)}
    frozen = {"setup_implementation": implementation_hash()}
    with ledger.connection() as conn:
        source = extract_panel(ledger, conn, "SYNTHETIC", variant, window, dates, study, frozen, cal, clock.now())
    result = stress_replay(source, scenario, gates, cfg, cal, market, variant, window, dates, study, frozen, clock.now())
    assert ledger.path.read_bytes() == before
    assert result["policy_sha256"] and result["derived_event_head"] != result["source_event_head"]
    assert result["affected_plans"] == [plan.id]
    assert "synthetic_evidence" in result["panel"]["failures"]
    if scenario in {"delay", "gap"}:
        assert not result["panel"]["trades"]
    else:
        assert result["panel"]["trades"][0]["exit_session"] == str(second)
        assert result["panel"]["trades"][0]["holding_sessions"] == 2


def test_missing_adverse_holding_path_is_unresolved_not_imputed(tmp_path):
    from app.swing.validation_stress import stress_replay
    from app.swing.validation_protocol import Window
    from app.swing.setups import implementation_hash
    fixture = replay_fixture(tmp_path)
    cfg, cal, clock, plan, ledger, market, engine = fixture
    capture(fixture)
    first = plan.first_entry_session
    second = cal.calendar.next_session(str(first)).date()
    market.day(plan, first, high=plan.target+1)
    advance(fixture, first)
    # Only benchmark is observed on the next date; missing stock path stays missing.
    market.day(plan, second)
    market.observations.pop((plan.ticker, second))
    advance(fixture, second)
    _, _, _, study = study_fixture()
    variant = study.variants[0].model_copy(update={"configuration": dict(plan.configuration)})
    window = Window(id="isolated", start=plan.session, end=second)
    dates = {"seed": str(plan.session), "sessions": [str(first), str(second)], "signal_end": str(plan.session)}
    frozen = {"setup_implementation": implementation_hash()}
    with ledger.connection() as conn:
        source = extract_panel(ledger, conn, "SYNTHETIC", variant, window, dates, study, frozen, cal, clock.now())
    result = stress_replay(source, "halt", Gates(execution_stress_fraction=1), cfg, cal, market,
                           variant, window, dates, study, frozen, clock.now())
    assert "unresolved_paths" in result["panel"]["failures"]
    assert "missing_or_duplicate_exact_marks" in result["panel"]["failures"]


def test_neighbor_search_budget_requires_distinct_nearby_strategy_parameters():
    _, _, _, study = study_fixture()
    material = study.model_dump(mode="json")
    neighbor = deepcopy(material["variants"][0])
    neighbor.update(name="low_volume", role="parameter", perturbation="SWING_BREAKOUT_RVOL")
    neighbor["configuration"]["SWING_BREAKOUT_RVOL"] *= .9
    neighbor["accounts"] = {k: v+"-neighbor" for k, v in neighbor["accounts"].items()}
    material["variants"].append(neighbor)
    material["search_budget"] = 2
    assert Study.model_validate(material).variants[1].config_hash != study.variants[0].config_hash
    neighbor["configuration"]["SWING_BREAKOUT_RVOL"] = 100
    with pytest.raises(ValueError, match="bounded numeric"):
        Study.model_validate(material)
    neighbor["configuration"]["SWING_BREAKOUT_RVOL"] = 1.5
    with pytest.raises(ValueError, match="Repeated configurations"):
        Study.model_validate(material)


def test_concurrent_holdout_open_claims_are_serialized(registered):
    from concurrent.futures import ThreadPoolExecutor
    _, cal, clock, study, registry, _ = registered
    clock.set_time(cal.calendar.session_close(str(study.holdout.end)).to_pydatetime()+timedelta(seconds=1))
    def claim(_):
        try:
            registry.claim_holdout(study.id, cal)
            return "claimed"
        except ValueError:
            return "consumed"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == ["claimed", "consumed"]


def test_runtime_change_cannot_reuse_a_registered_protocol(registered, monkeypatch):
    cfg, cal, clock, study, registry, _ = registered
    monkeypatch.setattr("app.swing.validation.runtime_fingerprint", lambda: {"numpy": "other"})
    with pytest.raises(ValueError, match="changed after registration"):
        ValidationService(registry, cfg, clock, cal).evaluate(study.id)


def test_cli_schema_and_signed_source_audit_is_read_only(tmp_path, monkeypatch, capsys):
    from scripts.swing_validate import main
    from app.market.clock import FakeClock
    from tests.fixtures.swing_source import create_source
    from scripts.swing_data import run_import
    from app.core.config import Settings
    from datetime import datetime, timezone
    source, universe = create_source(tmp_path)
    cfg = Settings(_env_file=None, NSE_UNIVERSE_PATH=str(universe), SWING_MIN_HISTORY_SESSIONS=3,
        SWING_DELIVERY_SESSIONS=3, SWING_DB_PATH=str(tmp_path/"observations.db"),
        SWING_ARTIFACT_DIR=str(tmp_path/"artifacts"), SWING_PAPER_DB_PATH=str(tmp_path/"paper.db"))
    clock = FakeClock(datetime(2026, 10, 7, 3, tzinfo=timezone.utc))
    imported = run_import(cfg, clock, source)
    before = Path(cfg.SWING_DB_PATH).read_bytes()
    monkeypatch.setattr("scripts.swing_validate.load_settings", lambda: cfg)
    monkeypatch.setattr("scripts.swing_validate.ValidationSettings", lambda: ValidationSettings(_env_file=None,
        VALIDATION_DB_PATH=str(tmp_path/"validation.db"), VALIDATION_SIGNING_KEY="SYNTHETIC-dedicated-signing-key-123456789"))
    assert main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out)["properties"]["capital_inr"]
    output = tmp_path/"audit.json"
    assert main(["audit", "--vintage", imported["snapshot_id"], "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["signature"] and "point_in_time_universe_unverified" in report["payload"]["failures"]
    assert report["payload"]["model_status"] == "RESEARCH_ONLY"
    assert Path(cfg.SWING_DB_PATH).read_bytes() == before
    assert not (tmp_path/"validation.db").exists() and not Path(cfg.SWING_PAPER_DB_PATH).exists()
    with pytest.raises(SystemExit):
        main(["audit", "--vintage", imported["snapshot_id"], "--output", str(output)])


def test_all_promotion_metrics_must_pass_and_low_samples_cannot_promote():
    panel = metric_panel(n=500, clustered=False)
    metrics = summarize([panel], Gates(bootstrap_replicates=100), 20)
    assert metric_failures(metrics, Gates(bootstrap_replicates=100)) == []
    poor = deepcopy(metrics)
    poor.update(max_drawdown=.50, max_exposure=.99, max_adv_participation=.25, fill_coverage=.1)
    assert {"drawdown_limit", "exposure_limit", "capacity_limit", "fill_coverage"} <= set(metric_failures(poor, Gates()))

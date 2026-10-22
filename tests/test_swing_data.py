"""SYNTHETIC S1 regression checks; no network, source credentials or production data."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from app.core.config import Settings
from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock
from app.providers.swing_archive import export_snapshot, file_hash, last_completed, load_selection
from app.swing.data import import_snapshot
from app.swing.models import ContextRequest, DailyHistoryProvider, DailyPrice, DatedContext, HistoryRequest
from app.swing.repository import SwingRepository
from scripts.swing_data import run_import
from tests.fixtures.swing_source import create_source


@pytest.fixture
def env(tmp_path):
    source, universe = create_source(tmp_path)
    cfg = Settings(_env_file=None, NSE_UNIVERSE_PATH=str(universe), SWING_MIN_HISTORY_SESSIONS=3,
                   SWING_DELIVERY_SESSIONS=3, SWING_IMPORT_BATCH_SIZE=1,
                   SWING_DB_PATH=str(tmp_path / "destination.db"), SWING_ARTIFACT_DIR=str(tmp_path / "artifacts"))
    clock = FakeClock(datetime(2026, 10, 7, 3, tzinfo=timezone.utc))  # 08:30 IST: last complete = Oct 6.
    return cfg, clock, source


def snapshot(env):
    cfg, clock, source = env
    cal = MarketCalendar("NSE", cfg)
    return export_snapshot(source, Path(cfg.SWING_ARTIFACT_DIR), load_selection(Path(cfg.NSE_UNIVERSE_PATH)),
                           cal, clock, cfg.SWING_BENCHMARK, cfg.SWING_SOURCE_TIMEOUT_SEC,
                           cfg.SWING_MIN_HISTORY_SESSIONS, cfg.SWING_DELIVERY_SESSIONS)


def test_real_universe_rules_read_only_selection_and_exact_coverage(env):
    cfg, clock, source = env
    before = file_hash(source)
    report = run_import(cfg, clock, source)
    assert report["summary"] == {"universe": 2, "symbols_with_prices": 2,
        "complete_trailing_price_histories": 1, "complete_trailing_delivery_histories": 1,
        "symbols_without_delivery": 1, "swing_eligible": 0}
    assert report["benchmark"]["rows"] == 0
    assert report["benchmark"]["source_inventory"] == ["^NSEI"]  # Never substitute for Nifty 500.
    beta = next(m for m in report["members"] if m["ticker"] == "TEST2.NS")
    assert beta["current_series"] == "BE" and beta["delivery_status"] == "unavailable"
    assert beta["historical_identity_verified"] is False
    assert beta["missing_price_sessions"] == ["2026-10-01", "2026-10-05"]
    assert file_hash(source) == before
    artifact = next((Path(cfg.SWING_ARTIFACT_DIR) / "snapshots").glob("*.jsonl")).read_text()
    assert "NEVER EXPORT" not in artifact and "OTHER.NS" not in artifact and "DUMMYTEST" not in artifact


def test_repeat_import_revisions_and_immutable_reports(env):
    cfg, clock, source = env
    first = run_import(cfg, clock, source)
    clock.sleep(30)
    again = run_import(cfg, clock, source)
    assert again["snapshot_id"] == first["snapshot_id"] and again["reused"]
    assert again["imported_at"] == first["imported_at"]
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE prices SET close=103 WHERE symbol='TEST1.NS' AND date='2026-10-06'")
    changed = run_import(cfg, clock, source)
    assert changed["snapshot_id"] != first["snapshot_id"]
    repo = SwingRepository(Path(cfg.SWING_DB_PATH))
    assert repo.report(first["snapshot_id"]) == first
    with repo.connection() as conn:
        assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
    for table in ("snapshots", "prices", "contexts", "reports", "rejects"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with repo.connection(write=True) as conn:
                # Add one reject when necessary so DELETE fires its row trigger.
                if table == "rejects":
                    conn.execute("INSERT INTO rejects VALUES(?,?,?,?,?)", (first["snapshot_id"], 999, None, None, "SYNTHETIC"))
                conn.execute(f"DELETE FROM {table}")


def test_unknown_basis_and_not_yet_available_provider_states(env):
    cfg, clock, source = env
    report = run_import(cfg, clock, source)
    repo = SwingRepository(Path(cfg.SWING_DB_PATH))
    request = HistoryRequest("TEST1.NS", date(2026,10,1), date(2026,10,6), clock.now(), report["snapshot_id"], "raw")
    assert repo.history(request).reason == "price_basis_unresolved_or_mismatched"
    assert repo.history(replace(request, as_of=clock.now()-timedelta(seconds=1))).reason == "vintage_not_available_at_decision"
    context = ContextRequest("TEST1.NS", "delivery", date(2026,10,6), clock.now(), report["snapshot_id"])
    row = repo.context(context)
    assert row.status == "available" and row.values["delivery_pct"] == 65
    assert row.published_at is None and not row.publication_verified
    assert repo.context(replace(context, as_of=clock.now()-timedelta(seconds=1))).status == "unavailable"
    assert repo.context(replace(context, ticker="TEST2.NS")).status == "unavailable"
    assert repo.context(replace(context, kind="earnings")).status == "unavailable"
    assert repo.context(replace(context, session=date(2026,10,5))).session == date(2026,10,5)
    assert repo.context(replace(context, session=date(2026,10,2))).status == "unavailable"  # No carry-forward.


@pytest.mark.parametrize("change,code", [
    ("UPDATE prices SET high=99 WHERE symbol='TEST1.NS' AND date='2026-10-06'", "INVALID_OBSERVATION"),
    ("UPDATE prices SET volume=0 WHERE symbol='TEST1.NS' AND date='2026-10-06'", "INVALID_OBSERVATION"),
    ("UPDATE delivery_data SET delivery_pct=75 WHERE date='2026-10-06'", "CONTRADICTORY_DELIVERY_PERCENTAGE"),
    ("UPDATE prices SET date='2026-10-07' WHERE symbol='TEST1.NS' AND date='2026-10-06'", "UNCOMPLETED_OR_FUTURE_SESSION"),
    ("UPDATE prices SET date='2026-10-02' WHERE symbol='TEST1.NS' AND date='2026-10-06'", "NON_SESSION"),
    ("UPDATE prices SET date='not-date' WHERE symbol='TEST1.NS' AND date='2026-10-06'", "INVALID_SESSION"),
    ("UPDATE prices SET date=NULL WHERE symbol='TEST1.NS' AND date='2026-10-06'", "INVALID_SESSION"),
    ("UPDATE delivery_data SET delivery_qty=101 WHERE date='2026-10-06'", "INVALID_DELIVERY")])
def test_invalid_rows_are_retained_as_rejections_without_fabrication(env, change, code):
    cfg, clock, source = env
    with sqlite3.connect(source) as conn:
        conn.execute(change)
    report = run_import(cfg, clock, source)
    assert report["rejected"][code] == 1
    assert report["accepted_prices"] + report["accepted_contexts"] + sum(report["rejected"].values()) == report["source_rows"]


def test_duplicate_rolls_back_all_batches_and_migration(env):
    cfg, clock, source = env
    with sqlite3.connect(source) as conn:
        conn.execute("INSERT INTO prices SELECT * FROM prices WHERE symbol='TEST1.NS' AND date='2026-10-06'")
    with pytest.raises(ValueError, match="duplicate"):
        run_import(cfg, clock, source)
    with sqlite3.connect(cfg.SWING_DB_PATH) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()


def test_snapshot_and_universe_hash_verification(env):
    cfg, clock, source = env
    report = run_import(cfg, clock, source)
    repo = SwingRepository(Path(cfg.SWING_DB_PATH))
    assert repo.verify(report["snapshot_id"])["artifact_verified"]
    artifact = Path(cfg.SWING_ARTIFACT_DIR) / "snapshots" / f"{report['snapshot_id']}.jsonl"
    original = artifact.read_bytes()
    artifact.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="hash"):
        repo.verify(report["snapshot_id"])
    with pytest.raises(ValueError, match="modified"):
        snapshot(env)
    artifact.write_bytes(original)
    universe = Path(cfg.SWING_ARTIFACT_DIR) / f"universe-{report['universe_sha256']}.csv"
    universe.write_text("SYNTHETIC tamper")
    with pytest.raises(ValueError, match="Universe artifact"):
        repo.verify(report["snapshot_id"])


def test_consistent_snapshot_while_source_writer_commits(env, monkeypatch):
    import app.providers.swing_archive as adapter
    original = adapter._evidence_value
    changed = False
    def change_after_read(value):
        nonlocal changed
        if not changed:
            changed = True
            with sqlite3.connect(env[2]) as conn:
                conn.execute("UPDATE prices SET close=104 WHERE symbol='TEST1.NS' AND date='2026-10-06'")
        return original(value)
    monkeypatch.setattr(adapter, "_evidence_value", change_after_read)
    captured = snapshot(env)
    rows = [json.loads(line) for line in captured.path.read_text().splitlines()[1:]]
    assert next(r["row"]["close"] for r in rows if r["table"] == "prices" and r["row"]["symbol"] == "TEST1.NS" and r["row"]["date"] == "2026-10-06") == 102
    with sqlite3.connect(env[2]) as conn:
        assert conn.execute("SELECT close FROM prices WHERE symbol='TEST1.NS' AND date='2026-10-06'").fetchone()[0] == 104


def test_calendar_pre_open_intraday_and_closure(env):
    cal = MarketCalendar("NSE", env[0])
    for hour, expected in [(3, "2026-10-06"), (6, "2026-10-06"), (11, "2026-10-07")]:
        assert last_completed(cal, datetime(2026,10,7,hour,tzinfo=timezone.utc)) == expected
    assert last_completed(cal, datetime(2026,1,15,12,tzinfo=timezone.utc)) == "2026-01-14"


def test_missing_source_schema_universe_and_same_destination_fail_closed(env):
    cfg, clock, source = env
    with pytest.raises(FileNotFoundError):
        run_import(cfg, clock, source.with_name("absent.db"))
    with pytest.raises(ValueError, match="differ"):
        run_import(cfg, clock, Path(cfg.SWING_DB_PATH))
    with sqlite3.connect(source) as conn:
        conn.execute("ALTER TABLE prices RENAME COLUMN close TO wrong")
    with pytest.raises(ValueError, match="schema"):
        snapshot(env)
    with pytest.raises(FileNotFoundError):
        load_selection(source.with_name("absent.csv"))


@pytest.mark.parametrize("key,value", [("SWING_MIN_HISTORY_SESSIONS",1), ("SWING_IMPORT_BATCH_SIZE",0),
    ("SWING_SOURCE_TIMEOUT_SEC",float("inf")), ("SWING_BENCHMARK", " "), ("SWING_DB_PATH", "")])
def test_s1_configuration_validation(key, value):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{key: value})


def test_dated_context_and_known_basis_contracts(env):
    now = env[1].now()
    args = dict(kind="delivery", ticker="TEST1.NS", session=date(2026,10,6), source="SYNTHETIC", ingested_at=now, status="available", values={"value": 1})
    for extra in [dict(ingested_at=now.replace(tzinfo=None)), dict(published_at=now+timedelta(seconds=1)),
                  dict(publication_verified=True), dict(values={"value":float('nan')}), dict(source=" ")]:
        with pytest.raises(ValueError):
            DatedContext(**{**args, **extra})
    with pytest.raises(ValueError):
        HistoryRequest("TEST1.NS", date(2026,10,1), date(2026,10,6), now, "vintage", "unknown")
    with pytest.raises(ValueError):
        ContextRequest("TEST1.NS", "delivery", date(2026,10,8), now, "vintage")


def test_unrelated_destination_database_is_not_migrated(env):
    cfg, clock, source = env
    with sqlite3.connect(cfg.SWING_DB_PATH) as conn:
        conn.execute("CREATE TABLE unrelated(value TEXT)")
    with pytest.raises(ValueError, match="unrelated"):
        run_import(cfg, clock, source)
    with sqlite3.connect(cfg.SWING_DB_PATH) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("unrelated",)]


def test_sealed_vintage_cannot_accept_late_observations(env):
    cfg, clock, source = env
    report = run_import(cfg, clock, source)
    repo = SwingRepository(Path(cfg.SWING_DB_PATH))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with repo.connection(write=True) as conn:
            conn.execute("INSERT INTO prices VALUES(?,?,?,?,?)", (report["snapshot_id"], "TEST1.NS", "2026-09-30", "raw", "{}"))


def test_known_basis_reader_never_stitches_vintages_and_requires_exact_sessions(env):
    cfg, clock, source = env
    report = run_import(cfg, clock, source)
    repo = SwingRepository(Path(cfg.SWING_DB_PATH))
    # SYNTHETIC independently declared raw vintage, representing another future adapter's canonical output.
    with repo.connection(write=True) as conn:
        conn.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?)", ("SYNTHETIC-raw-vintage", "SYNTHETIC", clock.now().isoformat(), clock.now().isoformat(), "SYNTHETIC", "{}"))
        row = DailyPrice(ticker="TEST1.NS", session=date(2026,10,6), open=100, high=105, low=95, close=102, volume=100, price_basis="raw")
        conn.execute("INSERT INTO prices VALUES(?,?,?,?,?)", ("SYNTHETIC-raw-vintage", row.ticker, row.session.isoformat(), row.price_basis, row.model_dump_json()))
        conn.execute("INSERT INTO reports VALUES(?,?)", ("SYNTHETIC-raw-vintage", "{}"))
    request = HistoryRequest("TEST1.NS", date(2026,10,6), date(2026,10,6), clock.now(), "SYNTHETIC-raw-vintage", "raw")
    assert repo.history(request).rows == (row,)
    assert repo.history(replace(request, start=date(2026,10,5))).reason == "missing_exact_sessions"
    assert repo.history(replace(request, vintage=report["snapshot_id"])).status == "unavailable"


def test_window_boundaries_and_index_close_only_do_not_invent_ohlcv(env):
    cfg, clock, source = env
    with sqlite3.connect(source) as conn:
        conn.execute("INSERT INTO prices VALUES('TEST1.NS','2026-09-30',100,105,95,102,100)")
        conn.execute("INSERT INTO benchmarks VALUES('^CRSLDX','2026-10-06',100)")
    report = run_import(cfg, clock, source)
    assert report["accepted_prices"] == 5
    assert report["benchmark"]["rows"] == 1 and report["benchmark"]["open_rows"] == 0
    with SwingRepository(Path(cfg.SWING_DB_PATH)).connection() as conn:
        payload = json.loads(conn.execute("SELECT payload FROM prices WHERE ticker='^CRSLDX'").fetchone()[0])
        assert payload["open"] is None and payload["volume"] is None
        assert not conn.execute("SELECT 1 FROM prices WHERE session='2026-09-30'").fetchone()


def test_failed_mid_import_does_not_commit_an_import_watermark(env, monkeypatch):
    import app.swing.data as data
    captured = snapshot(env)
    original = data._observation
    calls = 0
    def fail(table, raw, snap, cal, imported):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("SYNTHETIC interrupted import")
        return original(table, raw, snap, cal, imported)
    monkeypatch.setattr(data, "_observation", fail)
    repo = SwingRepository(Path(env[0].SWING_DB_PATH))
    with pytest.raises(RuntimeError):
        data.import_snapshot(captured, repo, MarketCalendar("NSE", env[0]), env[0], env[1])
    with sqlite3.connect(env[0].SWING_DB_PATH) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    monkeypatch.setattr(data, "_observation", original)
    assert data.import_snapshot(captured, repo, MarketCalendar("NSE", env[0]), env[0], env[1])["accepted_prices"] == 4


def test_cli_import_report_and_verification(env, monkeypatch, capsys, tmp_path):
    from scripts.swing_data import main
    cfg, clock, source = env
    output = tmp_path / "coverage.json"
    import scripts.swing_data as cli
    monkeypatch.setattr(cli, "load_settings", lambda: cfg)
    monkeypatch.setattr(cli, "SystemClock", lambda: clock)
    monkeypatch.setattr("sys.argv", ["swing_data", "import", "--source", str(source), "--report", str(output)])
    assert main() == 0
    report = json.loads(output.read_text())
    for command in ("report", "verify"):
        monkeypatch.setattr("sys.argv", ["swing_data", command, "--vintage", report["snapshot_id"]])
        assert main() == 0
    assert '"artifact_verified": true' in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["swing_data", "import", "--source", str(source), "--report", str(source)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_sqlite_uri_escaping_preserves_exact_source_and_destination(env):
    cfg, clock, source = env
    renamed = source.with_name("SYNTHETIC # source.db")
    # SQLite backup seals the test WAL into the newly named file without changing the original.
    with sqlite3.connect(source) as src, sqlite3.connect(renamed) as dest:
        src.backup(dest)
    cfg.SWING_DB_PATH = str(source.with_name("destination # verified.db"))
    assert run_import(cfg, clock, renamed)["accepted_prices"] == 4


def test_universe_comments_and_mutation_preserve_frozen_identity(env, monkeypatch):
    path = Path(env[0].NSE_UNIVERSE_PATH)
    path.write_bytes(b"# SYNTHETIC fixture\n" + path.read_bytes())
    assert set(load_selection(path).members) == {"TEST1.NS", "TEST2.NS"}
    from app.universe.nse import NSEUniverse
    original = NSEUniverse.load
    def changing(loader):
        rows = original(loader)
        path.write_text(path.read_text() + "\n")
        return rows
    monkeypatch.setattr(NSEUniverse, "load", changing)
    with pytest.raises(ValueError, match="changed during selection"):
        load_selection(path)

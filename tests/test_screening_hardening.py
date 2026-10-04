"""SYNTHETIC regressions: tradability defaults, instruments, bounded history."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.cache.history import history_capture_key
from app.cache.persistence import save_last_scan
from app.core.config import Settings
from app.core.filters import apply_stage1_filters, apply_stage2_pe_filter
from app.core.outcomes import FunnelTracker
from app.market.calendar import MarketCalendar
from app.services.state import ScanStateManager
from app.universe.nyse import NYSEUniverse
from tests.test_filters import make_dummy_indicator


def payload(timestamp="2026-10-01T10:20:00+00:00", partial=False, market="NSE"):
    return {
        "meta": {"market": market, "last_refreshed": timestamp,
                 "data_as_of": "2026-10-01T00:00:00+05:30"},
        "strategy_context": {"version": "SYNTHETIC", "MIN_PE": 1},
        "results": [{"ticker": "TEST1.NS", "name": "SYNTHETIC company", "market": market,
                     "price": 100, "pe": 10, "rsi": 60, "volume": 200000,
                     "avg_volume_20d": 100000, "volume_ratio": 2, "score": .5,
                     "session_partial": partial, "bar_date": "2026-10-01"}],
        "failed_symbols": [],
    }


def test_default_liquidity_filters_thin_spikes_without_data_failure():
    cfg = Settings(_env_file=None)
    tracker = FunnelTracker()
    assert not apply_stage1_filters(make_dummy_indicator(60, 10, 500), cfg, tracker)
    assert tracker.filtered_liquidity == 1 and tracker.failed == 0
    assert apply_stage1_filters(make_dummy_indicator(60, 2, cfg.MIN_AVG_VOLUME), cfg, FunnelTracker())


def test_default_pe_rejects_losses_but_keeps_legacy_override():
    cfg = Settings(_env_file=None)
    tracker = FunnelTracker()
    for pe in (-20, 0, .99):
        assert not apply_stage2_pe_filter(pe, cfg, tracker)
    assert tracker.filtered_pe == 3 and tracker.failed == 0
    assert apply_stage2_pe_filter(1, cfg, tracker)
    assert apply_stage2_pe_filter(-20, Settings(_env_file=None, MIN_PE=-500), FunnelTracker())


def test_instrument_descriptions_exclude_noise_and_keep_equity_trusts(tmp_path):
    names = {
        "TEST1": "SYNTHETIC Equity Common Stock",
        "TEST2": "SYNTHETIC Real Estate Trust Common Stock",
        "TEST3": "SYNTHETIC American Depositary Shares",
        "TEST4": "SYNTHETIC Class A Ordinary Shares",
        "TEST5": "SYNTHETIC 6% Subordinated Notes",
        "TEST6": "SYNTHETIC Senior Debentures",
        "TEST7": "SYNTHETIC Acquisition Common Stock",
        "TEST8": "SYNTHETIC Income Fund Common Shares",
        "TEST9": "SYNTHETIC Trust Shares of Beneficial Interest",
        "TEST10": "SYNTHETIC Unknown Instrument",
    }
    p = tmp_path / "SYNTHETIC.txt"
    p.write_text("ACT Symbol|Security Name|Exchange|ETF|Test Issue\n" +
                 "\n".join(f"{s}|{name}|N|N|N" for s, name in names.items()))
    assert {s.symbol for s in NYSEUniverse(p, Settings(_env_file=None)).load()} == {
        "TEST1", "TEST2", "TEST3", "TEST4"}


@pytest.mark.parametrize("timestamp,partial,expected", [
    ("2026-10-01T08:00:00+00:00", True, False),
    ("2026-10-01T10:19:00+00:00", False, False),
    ("2026-10-01T10:20:00+00:00", False, True),
    ("2026-10-01T10:21:00+00:00", True, False),
    ("2026-10-04T12:00:00+00:00", False, True),
    ("2026-10-01T10:20:00", False, False),
])
def test_canonical_capture_requires_completed_session(timestamp, partial, expected):
    key = history_capture_key(payload(timestamp, partial), Settings(_env_file=None), MarketCalendar("NSE"))
    assert (key is not None) == expected
    if key:
        assert key[0] == "2026-10-01" and key[2] == "post-close"


def test_intraday_sampling_and_market_specific_close():
    cfg, cal = Settings(_env_file=None, HISTORY_INTRADAY_INTERVAL_SEC=1800), MarketCalendar("NSE")
    a = history_capture_key(payload("2026-10-01T04:00:00+00:00", True), cfg, cal)
    b = history_capture_key(payload("2026-10-01T04:10:00+00:00", True), cfg, cal)
    c = history_capture_key(payload("2026-10-01T04:20:00+00:00", True), cfg, cal)
    assert a == b and a != c
    assert history_capture_key(payload("2026-10-01T20:20:00+00:00", market="NYSE"), cfg,
                               MarketCalendar("NYSE"))[2] == "post-close"


def test_repeat_history_keeps_first_signal_and_latest_snapshot(tmp_path):
    cfg, cal = Settings(_env_file=None), MarketCalendar("NSE")
    first = payload()
    key = history_capture_key(first, cfg, cal)
    save_last_scan("NSE", first, str(tmp_path), history_key=key)
    later = payload("2026-10-01T11:00:00+00:00")
    later["results"][0]["score"] = .9
    save_last_scan("NSE", later, str(tmp_path), history_key=key)
    with sqlite3.connect(tmp_path / "history.db") as conn:
        assert conn.execute("SELECT count(*) FROM scans").fetchone()[0] == 1
        assert conn.execute("SELECT score FROM signals").fetchone()[0] == .5
    snapshot = json.loads((tmp_path / "last_scan_NSE.json").read_text())
    assert snapshot["results"][0]["score"] == .9
    assert snapshot["meta"]["last_refreshed"] == later["meta"]["last_refreshed"]
    later["strategy_context"]["MIN_PE"] = 2
    save_last_scan("NSE", later, str(tmp_path), history_key=history_capture_key(later, cfg, cal))
    with sqlite3.connect(tmp_path / "history.db") as conn:
        assert conn.execute("SELECT count(*) FROM scans").fetchone()[0] == 2


def test_history_slot_is_atomic_across_writers_and_restarts(tmp_path):
    data = payload()
    key = history_capture_key(data, Settings(_env_file=None), MarketCalendar("NSE"))
    # Initialize before competing writes; both connections independently claim the slot.
    save_last_scan("NSE", data, str(tmp_path))
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(save_last_scan, "NSE", data, str(tmp_path), history_key=key) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)
    save_last_scan("NSE", data, str(tmp_path), history_key=key)
    with sqlite3.connect(tmp_path / "history.db") as conn:
        assert conn.execute("SELECT count(*) FROM scans").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM scan_history_slots WHERE scan_id IS NOT NULL").fetchone()[0] == 1


def test_slot_rolls_back_with_failed_signal_write(tmp_path):
    data = payload()
    save_last_scan("NSE", data, str(tmp_path))
    key = history_capture_key(data, Settings(_env_file=None), MarketCalendar("NSE"))
    with sqlite3.connect(tmp_path / "history.db") as conn:
        conn.execute("CREATE TRIGGER synthetic_fail BEFORE INSERT ON signals BEGIN SELECT RAISE(ABORT,'SYNTHETIC'); END")
    with pytest.raises(sqlite3.IntegrityError, match="SYNTHETIC"):
        save_last_scan("NSE", data, str(tmp_path), history_key=key)
    with sqlite3.connect(tmp_path / "history.db") as conn:
        assert conn.execute("SELECT count(*) FROM scan_history_slots").fetchone()[0] == 0
        conn.execute("DROP TRIGGER synthetic_fail")
    save_last_scan("NSE", data, str(tmp_path), history_key=key)
    with sqlite3.connect(tmp_path / "history.db") as conn:
        assert conn.execute("SELECT count(*) FROM scans").fetchone()[0] == 2


def test_runtime_policy_updates_snapshot_without_intraday_sqlite(tmp_path, monkeypatch):
    state = ScanStateManager.__new__(ScanStateManager)
    state.config = Settings(_env_file=None)
    state.calendars = {"NSE": MarketCalendar("NSE")}
    data = payload("2026-10-01T08:00:00+00:00", True)
    state.get_results_payload = lambda market: data

    def save(**kwargs):
        return save_last_scan(data_dir=str(tmp_path), **kwargs)

    monkeypatch.setattr("app.services.state.save_last_scan", save)
    state.persist_last_scan("NSE")
    assert (tmp_path / "last_scan_NSE.json").exists()
    assert not (tmp_path / "history.db").exists()
    state.config.HISTORY_MODE = "all"
    state.persist_last_scan("NSE")
    assert (tmp_path / "history.db").exists()


@pytest.mark.parametrize("values", [
    {"HISTORY_MODE": "unknown"}, {"HISTORY_INTRADAY_INTERVAL_SEC": -1},
    {"NYSE_REQUIRE_NAME_PATTERN": "["}, {"NYSE_EXCLUDE_NAME_PATTERN": "["},
])
def test_invalid_history_or_instrument_config_fails_fast(values):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)

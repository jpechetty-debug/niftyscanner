"""SYNTHETIC local-data extraction, provenance and existing importer checks."""
from datetime import datetime, date, timezone
import hashlib
import sqlite3
import io
import zipfile

import pytest

from app.core.config import Settings
from app.market.clock import FakeClock
from app.providers.swing_archive import file_hash
from app.swing.models import HistoryRequest
from app.swing.repository import SwingRepository
from scripts.swing_data import run_import
from scripts.swing_reuse_local import collect, index_bar, equity_rows
from tests.fixtures.swing_local_source import local_source, index_csv, bhavcopy_csv


def environment(tmp_path):
    source, universe = local_source(tmp_path)
    cfg = Settings(_env_file=None, NSE_UNIVERSE_PATH=str(universe), SWING_MIN_HISTORY_SESSIONS=3,
        SWING_DELIVERY_SESSIONS=3, SWING_DB_PATH=str(tmp_path/"SYNTHETIC-destination.db"),
        SWING_ARTIFACT_DIR=str(tmp_path/"SYNTHETIC-artifacts"))
    return source, cfg, FakeClock(datetime(2026, 10, 7, 3, tzinfo=timezone.utc))


@pytest.mark.parametrize("body", [index_csv(name="Nifty 50"), index_csv()+index_csv().split(b'\n')[1]+b'\n',
    index_csv(day="05-10-2026"), index_csv(close="200"), index_csv(close="nan")])
def test_index_identity_date_duplicate_and_invalid_values_fail(body):
    with pytest.raises(ValueError):
        index_bar(body, "2026-10-06", "^CRSLDX")


def test_index_missing_volume_stays_null_and_unknown_basis():
    bar = index_bar(index_csv(volume=""), "2026-10-06", "^CRSLDX")
    assert bar["volume"] is None and bar["open"] == 100
    with pytest.raises(ValueError, match="mapping"):
        index_bar(index_csv(), "2026-10-06", "^NSEI")


def test_selected_read_only_collection_and_s1_import_preserve_unknowns(tmp_path):
    source, cfg, clock = environment(tmp_path)
    before = file_hash(source)
    output = tmp_path/"SYNTHETIC-collection"
    manifest = collect(source, output, cfg, clock)
    assert file_hash(source) == before and manifest["source_unchanged"]
    assert manifest["index_rows"] == 1 and manifest["index_last"] == "2026-10-06"
    assert manifest["index_documents"][0]["publication_verified"] == 0
    assert len(list((output/"index_receipts").glob("*.csv"))) == 1
    with sqlite3.connect(output/"public.db") as cx:
        assert cx.execute("SELECT DISTINCT symbol FROM prices ORDER BY symbol").fetchall() == [("TEST1.NS",), ("TEST2.NS",), ("^CRSLDX",)]
        assert cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("prices",), ("delivery_data",)]
    first = run_import(cfg, clock, output/"public.db")
    again = run_import(cfg, clock, output/"public.db")
    assert first["snapshot_id"] == again["snapshot_id"] and again["reused"]
    assert first["benchmark"]["rows"] == first["benchmark"]["open_rows"] == 1
    assert first["summary"]["swing_eligible"] == 0
    request = HistoryRequest("^CRSLDX", date(2026,10,6), date(2026,10,6), clock.now(), first["snapshot_id"], "raw")
    assert SwingRepository(cfg.SWING_DB_PATH).history(request).reason == "price_basis_unresolved_or_mismatched"
    assert file_hash(source) == before
    with pytest.raises(ValueError, match="new output"):
        collect(source, output, cfg, clock)


@pytest.mark.parametrize("change", ["UPDATE history_blobs SET body=x'00'",
    "UPDATE history_documents SET ingested_at='2026-10-07T12:00:00+00:00'",
    "UPDATE history_documents SET ingested_at='2026-10-06T12:00:00'",
    "UPDATE history_documents SET source_uri='https://example.com/wrong.csv'"])
def test_bad_index_evidence_publishes_no_manifest(tmp_path, change):
    source, cfg, clock = environment(tmp_path)
    with sqlite3.connect(source) as cx:
        cx.execute(change)
    cx.close()
    output = tmp_path/"SYNTHETIC-collection"
    with pytest.raises(ValueError):
        collect(source, output, cfg, clock)
    assert not (output/"manifest.json").exists()


def test_source_change_during_collection_is_not_published(tmp_path, monkeypatch):
    from scripts import swing_reuse_local
    source, cfg, clock = environment(tmp_path)
    original = swing_reuse_local.fingerprints
    calls = 0
    def change(path):
        nonlocal calls
        calls += 1
        result = original(path)
        if calls == 2:
            result[str(path)] = "SYNTHETIC-changed"
        return result
    monkeypatch.setattr(swing_reuse_local, "fingerprints", change)
    output = tmp_path/"SYNTHETIC-collection"
    with pytest.raises(ValueError, match="Source changed"):
        collect(source, output, cfg, clock)
    assert not (output/"manifest.json").exists()


def test_conflicting_index_revisions_are_not_silently_selected(tmp_path):
    source, cfg, clock = environment(tmp_path)
    body = index_csv(close="104")
    sha = hashlib.sha256(body).hexdigest()
    with sqlite3.connect(source) as cx:
        cx.execute("INSERT INTO history_blobs VALUES(?,?)", (sha, body))
        cx.execute("INSERT INTO history_documents SELECT 'SYNTHETIC-revised',source_id,report_period,source_uri,?,ingested_at,published_at,publication_verified FROM history_documents LIMIT 1", (sha,))
    cx.close()
    output = tmp_path/"SYNTHETIC-collection"
    with pytest.raises(ValueError, match="Conflicting Nifty"):
        collect(source, output, cfg, clock)
    assert not (output/"manifest.json").exists()


@pytest.mark.parametrize("body", [bhavcopy_csv(day="2026-10-05"), bhavcopy_csv(close="200"),
    bhavcopy_csv(close="nan"), bhavcopy_csv(volume="0"),
    bhavcopy_csv()+bhavcopy_csv(series="BE").split(b'\n')[1]+b'\n'])
def test_bad_or_ambiguous_raw_equity_rows_are_explicit_rejects(body):
    bars, rejected = equity_rows(body, "2026-10-06", {"TEST1.NS"})
    assert bars == [] and rejected[0]["ticker"] == "TEST1.NS"


def test_raw_zip_and_legacy_csv_preserve_observations():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("SYNTHETIC.csv", bhavcopy_csv())
    zipped, rejects = equity_rows(archive.getvalue(), "2026-10-06", {"TEST1.NS"})
    legacy = b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY,TIMESTAMP\nTEST1,EQ,100,105,95,102,100,06-OCT-2026\n"
    assert equity_rows(legacy, "2026-10-06", {"TEST1.NS"}) == (zipped, rejects)
    assert zipped[0]["close"] == 102 and rejects == []
    assert equity_rows(legacy, "2026-10-06", {"TEST2.NS"}) == ([], [])


def test_unsupported_bhavcopy_columns_fail_before_claiming_an_empty_market():
    with pytest.raises(ValueError, match="Unsupported bhavcopy columns"):
        equity_rows(b"wrong,columns\nTEST1,102\n", "2026-10-06", {"TEST1.NS"})


def test_raw_collection_never_stitches_provider_cache_rows(tmp_path):
    source, cfg, clock = environment(tmp_path)
    body = bhavcopy_csv()
    sha = hashlib.sha256(body).hexdigest()
    with sqlite3.connect(source) as cx:
        cx.execute("INSERT INTO history_blobs VALUES(?,?)", (sha, body))
        cx.execute("INSERT INTO history_documents VALUES(?,?,?,?,?,?,?,?)", (
            "SYNTHETIC-bhavcopy", "nse-bhavcopy-observed-v1", "2026-10-06",
            "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20261006_F_0000.csv.zip", sha,
            "2026-10-06T12:00:00+00:00", None, 0))
        cx.execute("INSERT INTO history_documents SELECT 'SYNTHETIC-other-product',source_id,report_period,?,sha256,ingested_at,published_at,publication_verified FROM history_documents WHERE document_id='SYNTHETIC-bhavcopy'", (
            "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_06102026.csv",))
    cx.close()
    output = tmp_path/"SYNTHETIC-raw-collection"
    manifest = collect(source, output, cfg, clock, equity_source="nse_bhavcopy")
    assert manifest["selected_cache_rows"]["prices"] == 1 and len(manifest["equity_documents"]) == 1
    assert manifest["equity_parse_rejects"] == []
    assert len(manifest["excluded_equity_products"]) == 1
    report = run_import(cfg, clock, output/"public.db")
    assert report["summary"]["symbols_with_prices"] == 1
    assert report["summary"]["complete_trailing_price_histories"] == 0
    assert len(list((output/"equity_receipts").glob("*.csv"))) == 1

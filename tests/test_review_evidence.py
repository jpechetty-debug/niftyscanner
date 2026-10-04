"""SYNTHETIC archive fixtures; no production history or network access."""
import hashlib
import json
import sqlite3
import zipfile

import pytest

from scripts.review_evidence import inspect_payload, verify_archive


def archive_fixture(tmp_path):
    database = tmp_path / "synthetic.db"
    with sqlite3.connect(database) as conn:
        conn.executescript("CREATE TABLE signals (market TEXT,ticker TEXT);"
                           "CREATE TABLE signal_cohorts (id INTEGER);"
                           "CREATE TABLE signal_outcomes (status TEXT);")
    conn.close()
    files = {"history.db": database.read_bytes(),
             "official-nifty500.csv": b"Symbol,Company Name\nSYNTH1,SYNTHETIC Company\n",
             "local-nifty500.csv": b"Symbol,Company Name\nSYNTH1,SYNTHETIC Company\n",
             "benchmark.json": json.dumps({"sessions": {"2026-09-01": {"open": 100, "close": 101}},
                                           "expected_proxy_sessions": ["2026-09-01"]}).encode()}
    files["summary.json"] = json.dumps(inspect_payload(files)).encode()
    manifest = {"sha256": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}
    return files, manifest


@pytest.mark.parametrize("tampered", [False, True])
def test_portable_evidence_verifies_counts_and_detects_tampering(tmp_path, tampered):
    files, manifest = archive_fixture(tmp_path)
    if tampered:
        files["local-nifty500.csv"] += b"SYNTH2,SYNTHETIC Tampering\n"
    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", json.dumps(manifest))
    if tampered:
        with pytest.raises(ValueError, match="Hash mismatch"):
            verify_archive(path)
    else:
        result = verify_archive(path)
        assert result["local_rows"] == result["benchmark_sessions"] == 1
        assert result["database"]["signals_by_market"] == {}


@pytest.mark.parametrize("problem", ["constituents", "benchmark", "summary"])
def test_recomputed_claims_reject_internally_inconsistent_evidence(tmp_path, problem):
    files, _ = archive_fixture(tmp_path)
    if problem == "constituents":
        files["local-nifty500.csv"] += b"SYNTH2,SYNTHETIC Mismatch\n"
    elif problem == "benchmark":
        files["benchmark.json"] = json.dumps({"sessions": {"2026-09-01": {"open": 0, "close": 1}},
                                             "expected_proxy_sessions": ["2026-09-01"]}).encode()
    else:
        files["summary.json"] = b"{}"
    path = tmp_path / "synthetic.zip"
    manifest = {"sha256": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(ValueError):
        verify_archive(path)

"""SYNTHETIC evidence only; request reuse never relaxes production gates."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock
from app.swing.repository import SwingRepository
from app.swing.service import SwingResearchService, UnknownVintage
from app.swing.validation import audit_vintage
from app.swing.workspace import SwingWorkspace
from scripts.swing_data import run_import
from tests.fixtures.swing_source import create_source


@pytest.fixture
def evidence(tmp_path):
    source, universe = create_source(tmp_path)
    cfg = Settings(_env_file=None, SWING_ENABLED=True, PERFORMANCE_ENABLED=False,
        NSE_UNIVERSE_PATH=str(universe), SWING_MIN_HISTORY_SESSIONS=3, SWING_DELIVERY_SESSIONS=3,
        SWING_DB_PATH=str(tmp_path/"SYNTHETIC-observations.db"),
        SWING_PAPER_DB_PATH=str(tmp_path/"SYNTHETIC-paper.db"),
        SWING_ARTIFACT_DIR=str(tmp_path/"SYNTHETIC-artifacts"))
    clock = FakeClock(datetime(2026, 10, 7, 3, tzinfo=timezone.utc))
    report = run_import(cfg, clock, source)
    cal = MarketCalendar("NSE", cfg)
    repo = SwingRepository(Path(cfg.SWING_DB_PATH), calendar=cal)
    vintage = report["snapshot_id"]
    metadata = repo._metadata(vintage)
    header = json.loads(metadata["header"])
    root = Path(metadata["artifact_path"]).parent.parent
    paths = {"snapshot": Path(metadata["artifact_path"]),
             "universe": root/f"universe-{header['universe_sha256']}.csv",
             "reuse": root/f"reuse-{header['reuse_manifest_sha256']}.json"}
    return cfg, clock, cal, repo, vintage, paths


def workspace_for(evidence, monkeypatch):
    cfg, clock, cal, repo, _, _ = evidence
    workspace = SwingWorkspace(cfg, clock, cal, SimpleNamespace(controls=lambda: {}), None)
    workspace.repository = repo
    workspace.research = SwingResearchService(repo, cfg, clock, cal)
    # Exercise composition in a unit test only. No synthetic API/UI is started;
    # production synthetic exclusion is tested separately without this override.
    monkeypatch.setattr(workspace, "synthetic", lambda metadata: False)
    return workspace


def spy_reads(repo, monkeypatch):
    spy = Mock(wraps=repo._read_evidence)
    monkeypatch.setattr(repo, "_read_evidence", spy)
    return spy


def test_nested_scope_reuses_checks_but_rehashes_on_completion(evidence, monkeypatch):
    _, _, _, repo, vintage, _ = evidence
    spy = spy_reads(repo, monkeypatch)
    with repo.verification_scope():
        original = repo.verify(vintage)
        original["artifact_verified"] = False
        with repo.verification_scope():
            assert repo.verify(vintage)["artifact_verified"] is True
            assert audit_vintage(repo, vintage)["verification"]["artifact_verified"] is True
        assert spy.call_count == 1
    assert spy.call_count == 2
    assert repo._verification.get() is None
    with repo.verification_scope():
        repo.verify(vintage)
    assert spy.call_count == 4


@pytest.mark.parametrize("kind", ["snapshot", "universe", "reuse"])
@pytest.mark.parametrize("when", ["before", "during", "between"])
def test_each_artifact_is_checked_fresh_and_mid_request_edits_fail(evidence, kind, when):
    _, _, _, repo, vintage, paths = evidence
    path = paths[kind]
    original, stat = path.read_bytes(), path.stat()

    def tamper():
        # Same size, restored mtime: a size/mtime freshness cache misses this.
        path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert path.stat().st_size == stat.st_size
        assert path.stat().st_mtime_ns == stat.st_mtime_ns

    if when == "between":
        with repo.verification_scope():
            repo.verify(vintage)
    if when != "during":
        tamper()
    with pytest.raises(ValueError, match="hash mismatch"):
        with repo.verification_scope():
            repo.verify(vintage)
            if when == "during":
                tamper()
                repo.verify(vintage)  # Reused; closing hash must reject the read.
    assert repo._verification.get() is None
    path.write_bytes(original)
    with repo.verification_scope():
        assert repo.verify(vintage)["artifact_verified"]


@pytest.mark.parametrize("kind", ["snapshot", "universe", "reuse"])
def test_deleted_evidence_during_request_fails_closed(evidence, kind):
    _, _, _, repo, vintage, paths = evidence
    with pytest.raises(ValueError, match="immutable evidence is missing"):
        with repo.verification_scope():
            repo.verify(vintage)
            paths[kind].unlink()
    assert repo._verification.get() is None


def test_names_consume_verified_bytes_even_if_an_edit_is_undone(evidence, monkeypatch):
    workspace = workspace_for(evidence, monkeypatch)
    _, _, _, repo, vintage, paths = evidence
    path = paths["universe"]
    original = path.read_bytes()
    with repo.verification_scope():
        metadata, header = workspace.snapshot(vintage)
        path.write_bytes(original.replace(b"SYNTHETIC Alpha", b"SYNTHETIC WRONG"))
        try:
            assert workspace.names(metadata, header)["TEST1.NS"] == "SYNTHETIC Alpha"
        finally:
            path.write_bytes(original)


def test_failed_body_discards_verification_and_does_not_mask_original_error(evidence, monkeypatch):
    _, _, _, repo, vintage, paths = evidence
    spy = spy_reads(repo, monkeypatch)
    with pytest.raises(RuntimeError, match="SYNTHETIC failure"):
        with repo.verification_scope():
            repo.verify(vintage)
            paths["snapshot"].unlink()
            raise RuntimeError("SYNTHETIC failure")
    assert repo._verification.get() is None and spy.call_count == 1
    with pytest.raises(FileNotFoundError):
        repo.verify(vintage)
    assert spy.call_count == 2


def test_concurrent_requests_do_not_share_receipts(evidence, monkeypatch):
    _, _, _, repo, vintage, _ = evidence
    spy = spy_reads(repo, monkeypatch)
    barrier = Barrier(2)

    def read():
        with repo.verification_scope():
            repo.verify(vintage)
            barrier.wait(timeout=10)
            repo.verify(vintage)
        assert repo._verification.get() is None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(read) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)
    assert spy.call_count == 4


def test_scope_cannot_reuse_a_receipt_for_another_repository(evidence, monkeypatch):
    cfg, _, cal, repo, vintage, paths = evidence
    other = SwingRepository(Path(cfg.SWING_DB_PATH), calendar=cal)
    spy = spy_reads(other, monkeypatch)
    with repo.verification_scope():
        repo.verify(vintage)
        with other.verification_scope():
            other.verify(vintage)
        assert spy.call_count == 2


def test_workspace_nested_paths_use_two_passes_and_preserve_payload(evidence, monkeypatch):
    workspace = workspace_for(evidence, monkeypatch)
    _, _, _, repo, vintage, _ = evidence
    spy = spy_reads(repo, monkeypatch)
    first = workspace.view(vintage)
    assert spy.call_count == 2
    # An uncached read is an independent behavior oracle for every payload field.
    with monkeypatch.context() as patch:
        patch.setattr(repo, "verification_scope", nullcontext)
        second = workspace.view(vintage)
    assert first == second
    assert first["research"]["plans"] == []
    assert first["research"]["view_summary"]["state"] == "evaluation_unavailable"
    assert not Path(workspace.config.SWING_PAPER_DB_PATH).exists()


def test_workspace_rejects_changes_after_nested_research_and_recovers(evidence, monkeypatch):
    workspace = workspace_for(evidence, monkeypatch)
    _, _, _, repo, vintage, paths = evidence
    path = paths["reuse"]
    original = path.read_bytes()

    def late_tamper():
        path.write_bytes(original+b" ")
        return {}

    workspace.operations.controls = late_tamper
    with pytest.raises(ValueError, match="Reuse manifest hash mismatch"):
        workspace.view(vintage)
    assert repo._verification.get() is None
    path.write_bytes(original)
    workspace.operations.controls = lambda: {}
    assert workspace.view(vintage)["research"]["vintage"] == vintage


def test_standalone_audit_and_research_remain_fully_verified(evidence, monkeypatch):
    cfg, clock, cal, repo, vintage, paths = evidence
    service = SwingResearchService(repo, cfg, clock, cal)
    spy = spy_reads(repo, monkeypatch)
    audit_vintage(repo, vintage)
    service.results(vintage)
    service.data_quality(vintage)
    assert spy.call_count == 3
    paths["universe"].write_bytes(b"SYNTHETIC tamper")
    for operation in (lambda: audit_vintage(repo, vintage), lambda: service.results(vintage)):
        with pytest.raises(ValueError, match="Universe artifact hash mismatch"):
            operation()


def test_scope_preserves_visibility_and_synthetic_rejection(evidence):
    cfg, clock, cal, repo, vintage, _ = evidence
    service = SwingResearchService(repo, cfg, clock, cal)
    with repo.verification_scope():
        service.snapshot(vintage, clock.now())
        with pytest.raises(UnknownVintage):
            service.snapshot(vintage, clock.now()-timedelta(seconds=1))
    workspace = SwingWorkspace(cfg, clock, cal, SimpleNamespace(controls=lambda: {}), None)
    with pytest.raises(ValueError, match="Synthetic evidence"):
        workspace.view(vintage)


def test_scope_keeps_vintages_separate(evidence, monkeypatch):
    cfg, clock, cal, repo, first, _ = evidence
    source = Path(cfg.NSE_UNIVERSE_PATH).parent/"SYNTHETIC-source.db"
    clock.sleep(60)
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE prices SET close=103 WHERE symbol='TEST1.NS' AND date='2026-10-06'")
    second = run_import(cfg, clock, source)["snapshot_id"]
    assert first != second
    spy = spy_reads(repo, monkeypatch)
    with repo.verification_scope():
        repo.verify(first)
        repo.verify(second)
        repo.verify(first)
    assert [call.args[0] for call in spy.call_args_list] == [first, second, first, second]


def test_metadata_changes_invalidate_a_cached_receipt(evidence):
    _, _, _, repo, vintage, _ = evidence
    with pytest.raises(ValueError, match="metadata changed"):
        with repo.verification_scope():
            repo.verify(vintage)
            # Deliberately bypass the append-only trigger in this isolated
            # SYNTHETIC corruption test, never in a production store.
            with repo.connection(write=True) as conn:
                conn.execute("DROP TRIGGER immutable_snapshots_UPDATE")
                conn.execute("UPDATE snapshots SET source_uri='SYNTHETIC changed' WHERE id=?", (vintage,))
            repo.verify(vintage)
    assert repo._verification.get() is None


def test_missing_store_remains_unavailable_without_creating_it(tmp_path):
    path = tmp_path/"SYNTHETIC-absent.db"
    repo = SwingRepository(path)
    with pytest.raises(FileNotFoundError):
        with repo.verification_scope():
            repo.verify("0"*64)
    assert not path.exists() and repo._verification.get() is None

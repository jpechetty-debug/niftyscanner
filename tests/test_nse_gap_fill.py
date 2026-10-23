"""SYNTHETIC gap filling: no production source/network/provider calls."""
from copy import deepcopy
from datetime import date
import hashlib
import json
import sqlite3

import httpx
import pytest

from app.providers.swing_archive import file_hash
from app.swing.models import HistoryRequest
from app.swing.repository import SwingRepository
from scripts.swing_data import run_import
from scripts.swing_fill_gaps import plan, fill, fetch
from tests.fixtures.nse_gap_fill import gap_environment, receipts, delivery_csv


def test_plans_documents_once_and_preserves_existing_records(tmp_path):
    base, cfg, clock = gap_environment(tmp_path)
    before = file_hash(base/"public.db")
    docs = plan(base, cfg, clock)["documents"]
    assert {(d["kind"],d["session"]) for d in docs} == {(k,"2026-10-07") for k in ("equity","index","delivery")}
    # TEST2 has older absent rows, but those whole-market files already exist.
    assert len(docs) == 3
    old = run_import(cfg, clock, base/"public.db")
    output = tmp_path/"SYNTHETIC-filled"
    result = fill(base, receipts(tmp_path, docs, clock), output, cfg, clock)
    assert result["added"] == {"equity":1,"index":1,"delivery":1}
    assert result["parent_unchanged"] and not result["unavailable_documents"]
    assert file_hash(base/"public.db") == before
    fresh = run_import(cfg, clock, output/"public.db")
    assert fresh["summary"]["complete_trailing_price_histories"] == 1
    assert fresh["benchmark"]["rows"] == 3 and fresh["benchmark"]["missing_sessions"] == []
    assert fresh["summary"]["complete_trailing_delivery_histories"] == 1
    assert fresh["summary"]["swing_eligible"] == 0
    assert run_import(cfg, clock, output/"public.db")["reused"]
    repo = SwingRepository(cfg.SWING_DB_PATH)
    assert repo.report(old["snapshot_id"]) == old
    request = HistoryRequest("TEST1.NS", date(2026,10,5),date(2026,10,7),clock.now(),fresh["snapshot_id"],"raw")
    assert repo.history(request).reason == "price_basis_unresolved_or_mismatched"
    assert plan(output, cfg, clock)["documents"] == []
    with pytest.raises(ValueError, match="No missing document"):
        fill(output, [], tmp_path/"SYNTHETIC-unnecessary", cfg, clock)


@pytest.mark.parametrize("change", ["hash", "url", "status", "naive", "future", "duplicate", "wrong_body_day"])
def test_invalid_receipts_publish_no_manifest_and_preserve_base(tmp_path, change):
    base, cfg, clock = gap_environment(tmp_path)
    attempts = receipts(tmp_path, plan(base,cfg,clock)["documents"], clock)
    first = attempts[0]
    if change == "hash": first["sha256"] = "bad"
    elif change == "url": first["url"] = "https://example.com/wrong.zip"
    elif change == "status": first["status_code"] = 403
    elif change == "naive": first["observed_at"] = "2026-10-08T03:00:00"
    elif change == "future": first["observed_at"] = "2026-10-09T03:00:00+00:00"
    elif change == "duplicate": attempts.append(deepcopy(first))
    else:
        from tests.fixtures.nse_gap_fill import zip_equity
        body = zip_equity("2026-10-06")
        from pathlib import Path
        Path(first["file"]).write_bytes(body)
        first["sha256"] = hashlib.sha256(body).hexdigest()
    before = file_hash(base/"public.db")
    output = tmp_path/"SYNTHETIC-failed"
    with pytest.raises(ValueError):
        fill(base, attempts, output, cfg, clock)
    assert not (output/"manifest.json").exists()
    assert file_hash(base/"public.db") == before


def test_conflicting_overlap_is_not_overwritten(tmp_path):
    base,cfg,clock = gap_environment(tmp_path)
    with sqlite3.connect(base/"public.db") as cx:
        cx.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?)", ("TEST1.NS","2026-10-07",100,105,95,103,100))
    cx.close()
    path = base/"manifest.json"
    metadata=json.loads(path.read_text(encoding="utf-8"));metadata["stage_sha256"]=file_hash(base/"public.db")
    path.write_text(json.dumps(metadata),encoding="utf-8")
    attempts=receipts(tmp_path,plan(base,cfg,clock)["documents"],clock)
    with pytest.raises(ValueError,match="Conflicting existing"):
        fill(base,attempts,tmp_path/"SYNTHETIC-conflict",cfg,clock)


def test_adjusted_collection_and_tampered_parent_are_refused(tmp_path):
    base,cfg,clock=gap_environment(tmp_path)
    path=base/"manifest.json";original=json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**original,"equity_source":"cache"}),encoding="utf-8")
    with pytest.raises(ValueError,match="raw NSE"):
        plan(base,cfg,clock)
    path.write_text(json.dumps(original),encoding="utf-8")
    with sqlite3.connect(base/"public.db") as cx:
        cx.execute("UPDATE prices SET close=101 WHERE symbol='TEST1.NS'")
    cx.close()
    with pytest.raises(ValueError,match="hash mismatch"):
        plan(base,cfg,clock)


def test_delivery_percentage_is_not_recomputed_to_force_acceptance(tmp_path):
    from pathlib import Path
    base,cfg,clock=gap_environment(tmp_path)
    attempts=receipts(tmp_path,plan(base,cfg,clock)["documents"],clock)
    item=next(a for a in attempts if a["kind"]=="delivery")
    body=delivery_csv(pct="55");Path(item["file"]).write_bytes(body);item["sha256"]=hashlib.sha256(body).hexdigest()
    output=tmp_path/"SYNTHETIC-contradiction"
    fill(base,attempts,output,cfg,clock)
    result=run_import(cfg,clock,output/"public.db")
    assert result["rejected"]["CONTRADICTORY_DELIVERY_PERCENTAGE"]==1


@pytest.mark.parametrize("status", [200,403,302])
def test_fetch_is_bounded_and_records_real_status_without_redirects(tmp_path,status):
    base,cfg,clock=gap_environment(tmp_path)
    docs=plan(base,cfg,clock)["documents"][:1]
    calls=[]
    def respond(request):
        calls.append(request.url)
        clock.sleep(1)
        return httpx.Response(status,content=b"SYNTHETIC payload",headers={"Location":"https://example.com/redirect"})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError,match="request limit"):
            fetch(docs,tmp_path/"SYNTHETIC-too-many",clock,client=client,max_documents=0)
        assert calls==[]
        result=fetch(docs,tmp_path/"SYNTHETIC-download",clock,client=client,max_documents=1)
    assert len(calls)==1 and result[0]["status_code"]==status
    assert ("file" in result[0])==(status==200)
    assert result[0]["observed_at"]>result[0]["request_started_at"]

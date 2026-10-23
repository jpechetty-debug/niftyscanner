"""SYNTHETIC completed-session NSE document gaps; fictitious TEST tickers only."""
from datetime import datetime, timezone
import hashlib
import io
import json
import sqlite3
import zipfile

from app.core.config import Settings
from app.market.clock import FakeClock
from scripts.swing_reuse_local import collect
from tests.fixtures.swing_local_source import local_source, index_csv, bhavcopy_csv


def zip_equity(day="2026-10-07"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SYNTHETIC.csv", bhavcopy_csv(day=day))
    return buffer.getvalue()


def delivery_csv(pct="65"):
    return ("SYMBOL,SERIES,DATE1,TTL_TRD_QNTY,DELIV_QTY,DELIV_PER\n"
            f"TEST1,EQ,07-Oct-2026,100,65,{pct}\n").encode()


def gap_environment(root):
    source, universe = local_source(root)
    with sqlite3.connect(source) as cx:
        for day in ("2026-10-01", "2026-10-05", "2026-10-06"):
            compact = day.replace("-", "")
            body = zip_equity(day)
            sha = hashlib.sha256(body).hexdigest()
            cx.execute("INSERT INTO history_blobs VALUES(?,?)", (sha, body))
            cx.execute("INSERT INTO history_documents VALUES(?,?,?,?,?,?,?,?)", (
                "SYNTHETIC-equity-"+day, "nse-bhavcopy-observed-v1", day,
                f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{compact}_F_0000.csv.zip",
                sha, "2026-10-06T12:00:00+00:00", None, 0))
            if day != "2026-10-06":
                body = index_csv(day=datetime.fromisoformat(day).strftime("%d-%m-%Y"))
                sha = hashlib.sha256(body).hexdigest()
                cx.execute("INSERT INTO history_blobs VALUES(?,?)", (sha, body))
                cx.execute("INSERT INTO history_documents VALUES(?,?,?,?,?,?,?,?)", (
                    "SYNTHETIC-index-"+day, "nse-index-close-observed-v1", day,
                    "https://nsearchives.nseindia.com/content/indices/SYNTHETIC.csv", sha,
                    "2026-10-06T12:00:00+00:00", None, 0))
    cx.close()
    cfg = Settings(_env_file=None, NSE_UNIVERSE_PATH=str(universe), SWING_MIN_HISTORY_SESSIONS=3,
        SWING_DELIVERY_SESSIONS=3, SWING_DB_PATH=str(root/"SYNTHETIC-target.db"),
        SWING_ARTIFACT_DIR=str(root/"SYNTHETIC-evidence"))
    clock = FakeClock(datetime(2026,10,7,3,tzinfo=timezone.utc))
    base = root/"SYNTHETIC-base"
    collect(source, base, cfg, clock, equity_source="nse_bhavcopy")
    clock.set_time(datetime(2026,10,8,3,tzinfo=timezone.utc))
    return base, cfg, clock


def receipts(root, documents, clock):
    items = []
    for item in documents:
        day = item["session"]
        body = (zip_equity(day) if item["kind"] == "equity" else
                index_csv(day=datetime.fromisoformat(day).strftime("%d-%m-%Y")) if item["kind"] == "index" else delivery_csv())
        path = root/("SYNTHETIC-"+item["kind"]+".dat")
        path.write_bytes(body)
        items.append({**item, "file": str(path), "status_code": 200,
            "observed_at": clock.now().isoformat(), "sha256": hashlib.sha256(body).hexdigest()})
    return items

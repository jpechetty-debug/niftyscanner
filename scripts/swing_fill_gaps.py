"""Explicit NSE document planning, acquisition and inspection gap filling.

plan --base <raw-collection>
fetch --base <raw-collection> --output <new-receipt-dir> --max-documents N
apply --base <raw-collection> --receipts <attempts.json> --output <new-collection>
Publish the prepared public.db separately with scripts.swing_data import.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import csv
from datetime import date, datetime
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import zipfile

import httpx
import pandas as pd

from app.core.config import load_settings
from app.market.calendar import MarketCalendar
from app.market.clock import SystemClock
from app.providers.swing_archive import TABLES, file_hash, last_completed, load_selection
from app.swing.models import utc
from scripts.swing_reuse_local import equity_rows, index_bar


def archive_url(kind: str, session: str) -> str:
    day = date.fromisoformat(session)
    root = "https://nsearchives.nseindia.com"
    if kind == "equity":
        return f"{root}/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    if kind == "index":
        return f"{root}/content/indices/ind_close_all_{day:%d%m%Y}.csv"
    if kind == "delivery":
        return f"{root}/products/content/sec_bhavdata_full_{day:%d%m%Y}.csv"
    raise ValueError("Unknown NSE document kind")


def _base(base, cfg):
    base = Path(base).resolve(strict=True)
    content = (base/"manifest.json").read_bytes()
    metadata = json.loads(content)
    selection = load_selection(Path(cfg.NSE_UNIVERSE_PATH))
    if metadata.get("equity_source") != "nse_bhavcopy" or metadata.get("equity_product") != "NSE_CM_UDIFF_F_0000":
        raise ValueError("Gap filling requires a separate raw NSE UDiFF collection")
    if metadata["universe_sha256"] != selection.sha256 or metadata["benchmark"] != cfg.SWING_BENCHMARK:
        raise ValueError("Collection universe or benchmark changed; review before reuse")
    if file_hash(base/"public.db") != metadata["stage_sha256"]:
        raise ValueError("Parent staging database hash mismatch")
    return base, metadata, hashlib.sha256(content).hexdigest(), selection


def _unchanged(base, metadata, manifest_sha):
    if file_hash(base/"manifest.json") != manifest_sha or file_hash(base/"public.db") != metadata["stage_sha256"]:
        raise ValueError("Parent collection changed during gap filling")


def _plan(base, metadata, cfg, clock):
    calendar = MarketCalendar("NSE", cfg)
    end = last_completed(calendar, utc(clock.now()))
    sessions = calendar.calendar.sessions
    preceding = sessions[sessions <= pd.Timestamp(end)]
    prices = [str(s.date()) for s in preceding[-cfg.SWING_MIN_HISTORY_SESSIONS:]]
    delivery = [str(s.date()) for s in preceding[-cfg.SWING_DELIVERY_SESSIONS:]]
    known = {kind: {d["report_period"] for d in metadata.get(kind+"_documents", [])}
             for kind in ("equity", "index")}
    with closing(sqlite3.connect((base/"public.db").as_uri()+"?mode=ro", uri=True)) as cx:
        cx.execute("PRAGMA query_only=ON")
        known["delivery"] = {r[0] for r in cx.execute("SELECT DISTINCT date FROM delivery_data")}
    documents = [{"kind": kind, "session": day, "url": archive_url(kind, day)}
                 for kind, days in (("equity", prices), ("index", prices), ("delivery", delivery))
                 for day in days if day not in known[kind]]
    return {"last_completed_session": end, "price_window_start": prices[0],
            "delivery_window_start": delivery[0], "documents": documents,
            "note": "Only missing document dates; gaps within already collected files remain explicit"}


def plan(base, cfg, clock) -> dict:
    base, metadata, sha, _ = _base(base, cfg)
    result = _plan(base, metadata, cfg, clock)
    _unchanged(base, metadata, sha)
    return result


def fetch(documents, output, clock, *, client, max_documents: int):
    """One ordinary GET per planned file; explicit bound, no retry/redirect bypass."""
    if max_documents < 1 or len(documents) > max_documents:
        raise ValueError("Planned documents exceed the explicit request limit")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    attempts = []
    for item in documents:
        if item["url"] != archive_url(item["kind"], item["session"]):
            raise ValueError("Unexpected NSE archive URL")
        attempt = {**item, "request_started_at": utc(clock.now()).isoformat()}
        try:
            response = client.get(item["url"], follow_redirects=False)
            body = response.content
            attempt.update(observed_at=utc(clock.now()).isoformat(), status_code=response.status_code, bytes=len(body),
                content_type=response.headers.get("content-type"), sha256=hashlib.sha256(body).hexdigest())
            if response.status_code == 200:
                suffix = ".zip" if item["kind"] == "equity" else ".csv"
                path = output/(attempt["sha256"]+suffix)
                path.write_bytes(body)
                attempt["file"] = str(path)
        except httpx.HTTPError as exc:
            attempt.update(observed_at=utc(clock.now()).isoformat(), status="unavailable", error_type=type(exc).__name__)
        attempts.append(attempt)
    (output/"attempts.json").write_text(json.dumps(attempts, indent=2)+"\n", encoding="utf-8")
    return attempts


def delivery_rows(body, session, members):
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    fields = {str(k).strip().upper() for k in (reader.fieldnames or [])}
    required = {"SYMBOL", "SERIES", "DATE1", "TTL_TRD_QNTY", "DELIV_QTY", "DELIV_PER"}
    if not required <= fields:
        raise ValueError("Unsupported NSE delivery columns")
    rows, rejects, seen, ambiguous = {}, [], set(), set()
    for ordinal, original in enumerate(reader, start=2):
        row = {str(k).strip().upper(): (v or "").strip() for k, v in original.items() if k is not None}
        ticker = row.get("SYMBOL", "")+".NS"
        if ticker not in members or row.get("SERIES") not in {"EQ", "BE"}:
            continue
        try:
            if ticker in seen:
                ambiguous.add(ticker)
                raise ValueError("Ambiguous delivery series")
            seen.add(ticker)
            day = datetime.strptime(row["DATE1"], "%d-%b-%Y").date().isoformat()
            if day != session:
                raise ValueError("Delivery row and document session differ")
            rows[ticker] = {"symbol": ticker, "date": day,
                "volume": float(row["TTL_TRD_QNTY"].replace(",", "")),
                "delivery_qty": float(row["DELIV_QTY"].replace(",", "")),
                "delivery_pct": float(row["DELIV_PER"].replace(",", "")),
                "source": archive_url("delivery", session)}
        except ValueError as exc:
            rejects.append({"ticker": ticker, "session": session, "csv_record": ordinal, "reason": str(exc)})
    return [row for ticker, row in sorted(rows.items()) if ticker not in ambiguous], rejects


def _document_session(body, session):
    """A daily file's date must match even for rows outside the selected universe."""
    if body.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            files = [f for f in archive.infolist() if f.filename.lower().endswith(".csv")]
            if len(files) != 1:
                raise ValueError("Expected one daily CSV")
            body = archive.read(files[0])
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    count = 0
    for original in reader:
        row = {str(k).strip().upper(): (v or "").strip() for k,v in original.items() if k is not None}
        if row.get("TRADDT"):
            day = date.fromisoformat(row["TRADDT"]).isoformat()
        else:
            day = datetime.strptime(row.get("DATE1") or row.get("TIMESTAMP", ""), "%d-%b-%Y").date().isoformat()
        if day != session:
            raise ValueError("Daily document session mismatch")
        count += 1
    if not count:
        raise ValueError("Daily document has no dated rows")


def fill(base, attempts, output, cfg, clock) -> dict:
    """Append missing compatible rows in a fresh collection; never update old values."""
    base, metadata, manifest_sha, selection = _base(base, cfg)
    planned = _plan(base, metadata, cfg, clock)
    allowed = {(d["kind"], d["session"]) for d in planned["documents"]}
    if not allowed:
        raise ValueError("No missing document dates in the requested window")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    receipts = output/"receipts"
    receipts.mkdir()
    destination = output/"public.db"
    added = {"equity": 0, "index": 0, "delivery": 0}
    accepted, rejects, seen = [], [], set()
    with closing(sqlite3.connect((base/"public.db").as_uri()+"?mode=ro", uri=True)) as source:
        source.row_factory = sqlite3.Row
        source.execute("PRAGMA query_only=ON")
        source.execute("BEGIN")
        with closing(sqlite3.connect(destination)) as target, target:
            target.row_factory = sqlite3.Row
            for table in ("prices", "delivery_data"):
                columns = TABLES[table]
                target.execute(f"CREATE TABLE {table} ({','.join(columns)})")
                cursor = source.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY symbol,date")
                while batch := cursor.fetchmany(cfg.SWING_IMPORT_BATCH_SIZE):
                    target.executemany(f"INSERT INTO {table} VALUES({','.join('?' for _ in columns)})", batch)
                target.execute(f"CREATE UNIQUE INDEX {table}_key ON {table}(symbol,date)")
            for item in attempts:
                kind, day = item["kind"], item["session"]
                key = (kind, day)
                if key not in allowed or key in seen or item["url"] != archive_url(kind, day):
                    raise ValueError("Unexpected or duplicate gap receipt")
                seen.add(key)
                if item.get("status_code") != 200 or utc(datetime.fromisoformat(item["observed_at"])) > utc(clock.now()):
                    raise ValueError("Gap receipt unavailable or not yet observed")
                body = Path(item["file"]).read_bytes()
                if hashlib.sha256(body).hexdigest() != item["sha256"]:
                    raise ValueError("Gap receipt content hash mismatch")
                if kind != "index":
                    _document_session(body, day)
                if kind == "equity":
                    rows, failures = equity_rows(body, day, selection.members)
                elif kind == "index":
                    rows, failures = [index_bar(body, day, cfg.SWING_BENCHMARK)], []
                else:
                    rows, failures = delivery_rows(body, day, selection.members)
                rejects.extend({**failure, "kind": kind, "sha256": item["sha256"]} for failure in failures)
                table = "delivery_data" if kind == "delivery" else "prices"
                columns = TABLES[table]
                for row in rows:
                    existing = target.execute(f"SELECT {','.join(columns)} FROM {table} WHERE symbol=? AND date=?",
                                              (row["symbol"], day)).fetchone()
                    values = tuple(row[k] for k in columns)
                    if existing:
                        if tuple(existing) != values:
                            raise ValueError("Conflicting existing observation; no overwrite allowed")
                        continue
                    target.execute(f"INSERT INTO {table} VALUES({','.join('?' for _ in columns)})", values)
                    added[kind] += 1
                suffix = ".zip" if kind == "equity" else ".csv"
                artifact = receipts/(item["sha256"]+suffix)
                artifact.write_bytes(body)
                accepted.append({**item, "artifact": str(artifact), "report_period": day,
                    "source_uri": item["url"], "document_id": "nse-gap:"+item["sha256"],
                    "ingested_at": item["observed_at"], "published_at": None, "publication_verified": False})
        source.rollback()
    _unchanged(base, metadata, manifest_sha)
    manifest = {"format": "nse-gap-inspection-v1", "observed_at": utc(clock.now()).isoformat(),
        "parent_collection": str(base), "parent_manifest_sha256": manifest_sha,
        "parent_stage_sha256": metadata["stage_sha256"], "parent_unchanged": True,
        "equity_source": "nse_bhavcopy", "equity_product": "NSE_CM_UDIFF_F_0000",
        "universe_sha256": selection.sha256, "benchmark": cfg.SWING_BENCHMARK,
        **{k: planned[k] for k in ("last_completed_session", "price_window_start", "delivery_window_start")},
        **{kind+"_documents": [*metadata.get(kind+"_documents", []), *[d for d in accepted if d["kind"] == kind]]
           for kind in ("equity", "index", "delivery")},
        "added": added, "acquired_documents": accepted, "parse_rejects": rejects,
        "unavailable_documents": [d for d in planned["documents"] if (d["kind"], d["session"]) not in seen],
        "stage_sha256": file_hash(destination), "inspection_only": True,
        "limitations": metadata.get("limitations", [])+["S1 basis/identity/publication/eligibility remain unverified"]}
    (output/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "fetch", "apply"])
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--max-documents", type=int)
    args = parser.parse_args()
    cfg, clock = load_settings(), SystemClock()
    if args.command == "plan":
        result = plan(args.base, cfg, clock)
    elif args.command == "fetch":
        if args.output is None or args.max_documents is None:
            parser.error("fetch requires --output and an explicit --max-documents")
        with httpx.Client(timeout=cfg.SWING_SOURCE_TIMEOUT_SEC, trust_env=False,
                          headers={"User-Agent": "scanner-nifty500-local/1.0"}) as client:
            result = fetch(plan(args.base, cfg, clock)["documents"], args.output, clock,
                           client=client, max_documents=args.max_documents)
    else:
        if args.output is None or args.receipts is None:
            parser.error("apply requires --output and --receipts")
        result = fill(args.base, json.loads(args.receipts.read_text(encoding="utf-8")), args.output, cfg, clock)
        result = {k: result[k] for k in ("added", "parent_unchanged", "parse_rejects", "unavailable_documents")}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

"""Collect selected local market data for S1 inspection, without upgrading evidence.

python -m scripts.swing_reuse_local --source <lite.db> --output <new-directory>
Then explicitly import public.db with scripts.swing_data. No network or workers.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import zipfile
from urllib.parse import urlparse

import pandas as pd

from app.core.config import Settings, load_settings
from app.market.calendar import MarketCalendar
from app.market.clock import SystemClock
from app.providers.swing_archive import TABLES, file_hash, last_completed, load_selection
from app.swing.models import DailyPrice, utc

INDEX_SOURCE = "nse-index-close-observed-v1"
EQUITY_SOURCE = "nse-bhavcopy-observed-v1"


def equity_rows(body: bytes, report_day: str, members) -> tuple[list[dict], list[dict]]:
    """Select observed EQ/BE bhavcopy rows, retaining ambiguity/invalidity as rejects."""
    if body.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            files = [f for f in archive.infolist() if f.filename.lower().endswith(".csv")]
            if len(files) != 1:
                raise ValueError("Expected one bhavcopy CSV")
            body = archive.read(files[0])
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    values, rejected, seen, ambiguous = {}, [], set(), set()
    aliases = {"open": ("OPNPRIC", "OPEN_PRICE", "OPEN"), "high": ("HGHPRIC", "HIGH_PRICE", "HIGH"),
        "low": ("LWPRIC", "LOW_PRICE", "LOW"), "close": ("CLSPRIC", "CLOSE_PRICE", "CLOSE"),
        "volume": ("TTLTRADGVOL", "TTL_TRD_QNTY", "TOTTRDQTY")}
    fields = {str(k).strip().upper() for k in (reader.fieldnames or [])}
    for alternatives in [("TCKRSYMB", "SYMBOL"), ("SCTYSRS", "SERIES"),
                         ("TRADDT", "DATE1", "TIMESTAMP"), *aliases.values()]:
        if not fields.intersection(alternatives):
            raise ValueError("Unsupported bhavcopy columns")
    for ordinal, original in enumerate(reader, start=2):
        row = {str(k).strip().upper(): v.strip() if v else "" for k, v in original.items() if k is not None}
        def cell(*names):
            return next((row[n] for n in names if row.get(n)), "")
        ticker = cell("TCKRSYMB", "SYMBOL")+".NS"
        if ticker not in members or cell("SCTYSRS", "SERIES") not in {"EQ", "BE"}:
            continue
        try:
            if ticker in seen:
                ambiguous.add(ticker)
                raise ValueError("Ambiguous traded series")
            seen.add(ticker)
            if cell("TRADDT"):
                day = datetime.strptime(cell("TRADDT"), "%Y-%m-%d").date().isoformat()
            else:
                day = datetime.strptime(cell("DATE1", "TIMESTAMP"), "%d-%b-%Y").date().isoformat()
            if day != report_day:
                raise ValueError("Bhavcopy row and document session differ")
            numbers = {key: float(cell(*names).replace(",", "")) for key, names in aliases.items()}
            bar = DailyPrice(ticker=ticker, session=day, price_basis="unknown", **numbers)
            values[ticker] = {"symbol": ticker, "date": day, **{k: getattr(bar, k) for k in aliases}}
        except ValueError as exc:
            rejected.append({"ticker": ticker, "session": report_day, "csv_record": ordinal, "reason": str(exc)})
    return [bar for ticker, bar in sorted(values.items()) if ticker not in ambiguous], rejected


def index_bar(body: bytes, report_day: str, benchmark: str) -> dict:
    """Map the exact NSE Nifty 500 price-index CSV row; never substitute an index."""
    if benchmark != "^CRSLDX":
        raise ValueError("No documented local CSV identity mapping for this benchmark")
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    rows = [r for r in reader if r.get("Index Name", "").strip().casefold() == "nifty 500"]
    if len(rows) != 1:
        raise ValueError("Expected exactly one Nifty 500 price-index row")
    row = rows[0]
    day = datetime.strptime(row["Index Date"].strip(), "%d-%m-%Y").date().isoformat()
    if day != report_day:
        raise ValueError("Index row and document session differ")
    def number(key):
        return float(row[key].strip().replace(",", ""))
    volume = row.get("Volume", "").strip()
    bar = DailyPrice(ticker=benchmark, session=day, instrument="index",
        open=number("Open Index Value"), high=number("High Index Value"),
        low=number("Low Index Value"), close=number("Closing Index Value"),
        volume=float(volume.replace(",", "")) if volume else None, price_basis="unknown")
    return {"symbol": benchmark, "date": day, **{k: getattr(bar, k) for k in ("open", "high", "low", "close", "volume")}}


def fingerprints(path: Path) -> dict:
    """Bookend database/WAL hashes, without bypassing SQLite locking or WAL state."""
    # SQLite may create an empty WAL/SHM pair for a WAL-mode read-only reader.
    # A zero-byte WAL contains no frames; normalize it to absent. Any nonempty
    # WAL is hashed normally, so committed changes are still detected.
    return {str(p): file_hash(p) if p.exists() and (p == path or p.stat().st_size > 0) else None
            for p in (path, Path(str(path)+"-wal"))}


def collect(source: Path, output: Path, cfg: Settings, clock, *, equity_source="cache") -> dict:
    """Write a new, bounded public staging database and hashed index receipts."""
    source, output = Path(source).resolve(strict=True), Path(output).resolve()
    if source.is_relative_to(output) or output.exists():
        raise ValueError("Use a new output directory separate from the source")
    if cfg.SWING_BENCHMARK != "^CRSLDX":
        raise ValueError("No documented local CSV identity mapping for this benchmark")
    if equity_source not in {"cache", "nse_bhavcopy"}:
        raise ValueError("Unknown local equity collection mode")
    selection = load_selection(Path(cfg.NSE_UNIVERSE_PATH))
    calendar = MarketCalendar("NSE", cfg)
    observed = utc(clock.now())
    completed = last_completed(calendar, observed)
    sessions = calendar.calendar.sessions
    preceding = sessions[sessions <= pd.Timestamp(completed)]
    price_start = preceding[-cfg.SWING_MIN_HISTORY_SESSIONS].date().isoformat()
    delivery_start = preceding[-cfg.SWING_DELIVERY_SESSIONS].date().isoformat()
    before = fingerprints(source)
    output.mkdir(parents=True, exist_ok=False)
    receipts = output/"index_receipts"
    receipts.mkdir()
    destination = output/"public.db"
    counts, documents, index_rows = {}, [], {}
    equity_documents, equity_rejects, excluded_equity_products = [], [], []
    with closing(sqlite3.connect(source.as_uri()+"?mode=ro", uri=True,
                                timeout=cfg.SWING_SOURCE_TIMEOUT_SEC)) as cx:
        cx.row_factory = sqlite3.Row
        cx.execute("PRAGMA query_only=ON")
        cx.execute("BEGIN")
        with closing(sqlite3.connect(destination)) as target, target:
            for table in ("prices", "delivery_data"):
                columns = TABLES[table]
                target.execute(f"CREATE TABLE {table} ({','.join(columns)})")
                counts[table] = 0
                if table == "prices" and equity_source == "nse_bhavcopy":
                    continue
                names = sorted(selection.members)
                marks = ",".join("?" for _ in names)
                start = delivery_start if table == "delivery_data" else price_start
                rows = cx.execute(f"SELECT {','.join(columns)} FROM {table} WHERE symbol IN ({marks}) "
                    "AND (date>=? OR date IS NULL OR length(date)!=10) ORDER BY symbol,date", (*names, start))
                counts[table] = 0
                while batch := rows.fetchmany(cfg.SWING_IMPORT_BATCH_SIZE):
                    target.executemany(f"INSERT INTO {table} VALUES({','.join('?' for _ in columns)})", batch)
                    counts[table] += len(batch)
            query = """SELECT d.document_id,d.report_period,d.source_uri,d.sha256,d.ingested_at,
                d.published_at,d.publication_verified,b.body FROM history_documents d
                LEFT JOIN history_blobs b ON b.sha256=d.sha256
                WHERE d.source_id=? AND d.report_period>=? ORDER BY d.report_period,d.document_id"""
            if equity_source == "nse_bhavcopy":
                equity_receipts = output/"equity_receipts"
                equity_receipts.mkdir()
                # Never choose among multiple source revisions for one session.
                seen_days = set()
                for row in cx.execute(query, (EQUITY_SOURCE, price_start)):
                    day = row["report_period"]
                    uri = urlparse(row["source_uri"])
                    if uri.scheme != "https" or uri.hostname not in {"nsearchives.nseindia.com", "archives.nseindia.com"} or uri.username or uri.password:
                        raise ValueError("Unexpected bhavcopy report source URI")
                    # The source ID contains both UDiFF and security/delivery
                    # products. Freeze one declared product, not a last-row choice.
                    expected = f"/content/cm/BhavCopy_NSE_CM_0_0_0_{day.replace('-', '')}_F_0000.csv.zip"
                    if uri.path != expected:
                        excluded_equity_products.append({k: row[k] for k in row.keys() if k != "body"})
                        continue
                    if day in seen_days:
                        raise ValueError("Multiple bhavcopy revisions require review")
                    seen_days.add(day)
                    if utc(datetime.fromisoformat(row["ingested_at"])) > observed or day > completed:
                        raise ValueError("Bhavcopy report not available/completed at collection")
                    body = bytes(row["body"])
                    if hashlib.sha256(body).hexdigest() != row["sha256"]:
                        raise ValueError("Bhavcopy document content hash mismatch")
                    bars, failures = equity_rows(body, day, selection.members)
                    equity_rejects.extend({**failure, "document_id": row["document_id"]} for failure in failures)
                    columns = TABLES["prices"]
                    target.executemany(f"INSERT INTO prices VALUES({','.join('?' for _ in columns)})",
                                       [tuple(bar[k] for k in columns) for bar in bars])
                    counts["prices"] += len(bars)
                    suffix = ".zip" if body.startswith(b"PK") else ".csv"
                    (equity_receipts/(row["sha256"]+suffix)).write_bytes(body)
                    equity_documents.append({k: row[k] for k in row.keys() if k != "body"})
            for row in cx.execute(query, (INDEX_SOURCE, price_start)):
                stamp = datetime.fromisoformat(row["ingested_at"])
                if utc(stamp) > observed:
                    raise ValueError("Index document is not available at collection time")
                if row["report_period"] > completed:
                    raise ValueError("Uncompleted index report needs explicit review")
                uri = urlparse(row["source_uri"])
                if uri.scheme != "https" or uri.hostname not in {"nsearchives.nseindia.com", "archives.nseindia.com"} or uri.username or uri.password:
                    raise ValueError("Unexpected index report source URI")
                body = bytes(row["body"])
                if hashlib.sha256(body).hexdigest() != row["sha256"]:
                    raise ValueError("Index document content hash mismatch")
                bar = index_bar(body, row["report_period"], cfg.SWING_BENCHMARK)
                if bar["date"] in index_rows and index_rows[bar["date"]] != bar:
                    raise ValueError("Conflicting Nifty 500 revisions require review")
                index_rows[bar["date"]] = bar
                (receipts/(row["sha256"]+".csv")).write_bytes(body)
                documents.append({k: row[k] for k in row.keys() if k != "body"})
            columns = TABLES["prices"]
            for day, bar in sorted(index_rows.items()):
                target.execute(f"INSERT INTO prices VALUES({','.join('?' for _ in columns)})",
                               tuple(bar[k] for k in columns))
            target.execute("CREATE INDEX prices_symbol_date ON prices(symbol,date)")
            target.execute("CREATE INDEX delivery_symbol_date ON delivery_data(symbol,date)")
        cx.rollback()
    after = fingerprints(source)
    if before != after:
        raise ValueError("Source changed during collection; no manifest published")
    manifest = {"format": "local-public-inspection-collection-v1", "observed_at": observed.isoformat(),
        "source_uri": source.as_uri(), "source_before": before, "source_after": after,
        "source_unchanged": True, "universe_sha256": selection.sha256, "universe": len(selection.members),
        "last_completed_session": completed, "price_window_start": price_start,
        "delivery_window_start": delivery_start, "selected_cache_rows": counts,
        "equity_source": equity_source, "equity_documents": equity_documents, "equity_parse_rejects": equity_rejects,
        "equity_product": "NSE_CM_UDIFF_F_0000" if equity_source == "nse_bhavcopy" else "provider_cache",
        "excluded_equity_products": excluded_equity_products,
        "benchmark": cfg.SWING_BENCHMARK, "index_name": "Nifty 500", "index_rows": len(index_rows),
        "index_first": min(index_rows, default=None), "index_last": max(index_rows, default=None),
        "index_documents": documents, "stage_sha256": file_hash(destination),
        "collector_sha256": file_hash(Path(__file__)), "inspection_only": True,
        "limitations": ["Cache price basis/identity/publication remain unknown under S1",
            "NSE source URL and content hashes do not independently authenticate the publisher",
            "Original index reports retain their recorded ingestion/publication metadata",
            "No context, calendar, eligibility, worker or configuration policy changed"]}
    (output/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--equity-source", choices=["cache", "nse_bhavcopy"], default="cache")
    args = parser.parse_args()
    result = collect(args.source, args.output, load_settings(), SystemClock(), equity_source=args.equity_source)
    print(json.dumps({k: result[k] for k in ("universe", "selected_cache_rows", "index_rows", "index_first", "index_last", "source_unchanged")}, indent=2))


if __name__ == "__main__":
    main()

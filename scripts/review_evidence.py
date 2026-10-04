"""Build or verify a portable review archive; verification needs only Python stdlib.

Build from the repository root after collecting real probes under logs/review-audit.
Hashes detect changes to bundled evidence; they do not authenticate Yahoo/NSE data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def database_summary(path: Path) -> dict:
    """Read integrity and counts without modifying the evidence database."""
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return {
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "signals_by_market": dict(conn.execute("SELECT market,count(*) FROM signals GROUP BY market")),
            "synthetic_signals": conn.execute("SELECT count(*) FROM signals WHERE ticker LIKE 'TEST%'").fetchone()[0],
            "cohorts": conn.execute("SELECT count(*) FROM signal_cohorts").fetchone()[0],
            "outcomes_by_status": dict(conn.execute("SELECT status,count(*) FROM signal_outcomes GROUP BY status")),
        }
    finally:
        conn.close()


def inspect_payload(files: dict[str, bytes]) -> dict:
    """Recompute claims from the supplied bytes, without network or app imports."""
    def rows(name):
        return list(csv.DictReader(io.StringIO(files[name].decode("utf-8-sig"))))

    official, local = rows("official-nifty500.csv"), rows("local-nifty500.csv")
    official_symbols, local_symbols = [r["Symbol"] for r in official], [r["Symbol"] for r in local]
    if len(set(official_symbols)) != len(official_symbols) or len(set(local_symbols)) != len(local_symbols):
        raise ValueError("Duplicate constituent symbols")
    if set(official_symbols) != set(local_symbols):
        raise ValueError("Official and local constituent symbols differ")
    benchmark = json.loads(files["benchmark.json"])
    quotes = benchmark["sessions"]
    if not quotes or set(quotes) != set(benchmark["expected_proxy_sessions"]):
        raise ValueError("Benchmark sessions do not match the recorded proxy schedule")
    if not all(math.isfinite(q[k]) and q[k] > 0 for q in quotes.values() for k in ("open", "close")):
        raise ValueError("Invalid benchmark Open/Close")
    with tempfile.TemporaryDirectory(prefix="screener-evidence-") as temp:
        path = Path(temp) / "history.db"
        path.write_bytes(files["history.db"])
        database = database_summary(path)
    if database["integrity"] != "ok" or database["synthetic_signals"]:
        raise ValueError("Invalid or synthetic production history")
    return {"official_rows": len(official), "local_rows": len(local),
            "benchmark_sessions": len(quotes), "database": database}


def verify_archive(path: Path) -> dict:
    """Verify every hash and independently recompute counts in a fixed temp path."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive members")
        manifest = json.loads(archive.read("manifest.json"))
        if set(names) != set(manifest["sha256"]) | {"manifest.json"}:
            raise ValueError("Archive members do not match manifest")
        files = {name: archive.read(name) for name in manifest["sha256"]}
    for name, digest in manifest["sha256"].items():
        if hashlib.sha256(files[name]).hexdigest() != digest:
            raise ValueError(f"Hash mismatch: {name}")
    result = inspect_payload(files)
    if result != json.loads(files["summary.json"]):
        raise ValueError("Recomputed evidence differs from summary")
    return result


def build_archive(root: Path, output: Path) -> dict:
    """Back up real SQLite consistently and package existing observed evidence."""
    audit = root / "logs/review-audit"
    sources = {
        "official-nifty500.csv": root / "logs/universe-audit/official-nifty500-2026-10-04.csv",
        "official-source.json": root / "logs/universe-audit/comparison.json",
        "local-nifty500.csv": root / "data/nifty500.csv",
        "benchmark.json": audit / "benchmark.json",
        "fundamentals.json": audit / "fundamentals.json",
        "nyse.json": audit / "nyse.json",
        "nyse-changes.csv": audit / "nyse-changes.csv",
        "local-otherlisted.txt": root / "data/otherlisted.txt",
        "verify.py": Path(__file__),
    }
    files = {name: path.read_bytes() for name, path in sources.items()}
    with tempfile.TemporaryDirectory(prefix="screener-backup-") as temp:
        path = Path(temp) / "history.db"
        source = sqlite3.connect((root / "data/history.db").resolve().as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        files["history.db"] = path.read_bytes()
    summary = inspect_payload(files)
    files["summary.json"] = json.dumps(summary, indent=2).encode()
    files["README.txt"] = (
        "Observed real data captured 2026-10-04, not synthetic fixtures.\n"
        "Extract verify.py and run: python verify.py --verify <archive.zip>\n"
        "Verification requires no network or installed project dependencies.\n"
        "The database is a consistent backup, not a copied live WAL file.\n"
        "Constituents match the bundled official CSV; latest rebalance dates are not independently verified.\n"
        "Benchmark checks validate Yahoo prices against the recorded XBOM proxy schedule, not official opens.\n"
        "Hashes detect archive changes, not source authenticity. EPS probes cover two names only.\n"
        "Historical signals predate corrected filters; pending outcomes are not measured hit rates.\n"
    ).encode()
    for name in ("pytest.txt", "runtime.json"):
        if (audit / name).exists():
            files[name] = (audit / name).read_bytes()
    manifest = {"built_at": datetime.now(timezone.utc).isoformat(),
                "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    return verify_archive(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--output", type=Path, default=Path("docs/evidence/review-evidence-2026-10-04.zip"))
    args = parser.parse_args()
    result = verify_archive(args.verify) if args.verify else build_archive(Path.cwd(), args.output)
    print(json.dumps(result, indent=2))

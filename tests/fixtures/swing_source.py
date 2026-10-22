"""SYNTHETIC local SQLite cache and universe: fictitious TEST1/TEST2 only."""
from pathlib import Path
import sqlite3


def create_source(root: Path) -> tuple[Path, Path]:
    source, universe = root / "SYNTHETIC-source.db", root / "SYNTHETIC-universe.csv"
    universe.write_text("Symbol,Company Name,Series,ISIN Code\nTEST1,SYNTHETIC Alpha,EQ,SYNTHETIC-1\n"
                        "TEST2,SYNTHETIC Beta,BE,SYNTHETIC-2\nDUMMYTEST,SYNTHETIC placeholder,EQ,\n"
                        "TESTUNIT,SYNTHETIC unit,RR,\n", encoding="utf-8")
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE prices(symbol TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL,volume INTEGER);
            CREATE TABLE delivery_data(symbol TEXT,date TEXT,volume REAL,delivery_qty REAL,delivery_pct REAL,source TEXT);
            CREATE TABLE benchmarks(symbol TEXT,date TEXT,close REAL);
            CREATE TABLE private_accounts(secret TEXT);
            INSERT INTO private_accounts VALUES('SYNTHETIC PRIVATE: NEVER EXPORT');
        """)
        for day in ("2026-10-01", "2026-10-05", "2026-10-06"):
            conn.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?)", ("TEST1.NS", day, 100, 105, 95, 102, 100))
            conn.execute("INSERT INTO delivery_data VALUES(?,?,?,?,?,?)", ("TEST1.NS", day, 100, 65, 65, "SYNTHETIC"))
            conn.execute("INSERT INTO benchmarks VALUES(?,?,?)", ("^NSEI", day, 100))
        conn.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?)", ("TEST2.NS", "2026-10-06", 100, 105, 95, 102, 100))
        conn.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?)", ("OTHER.NS", "2026-10-06", 100, 105, 95, 102, 100))
    conn.close()  # Commit context managers do not close SQLite; finish WAL checkpoint before source hashing.
    return source, universe

"""SYNTHETIC index reports and private sentinel; never real market observations."""
import hashlib
import sqlite3

from tests.fixtures.swing_source import create_source


def index_csv(name="Nifty 500", day="06-10-2026", close="102", volume="100"):
    return ("Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Volume\n"
            f"{name},{day},100,105,95,{close},{volume}\n").encode()


def bhavcopy_csv(series="EQ", day="2026-10-06", close="102", volume="100"):
    return ("TradDt,TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
            f"{day},TEST1,{series},SYNTHETIC-1,100,105,95,{close},{volume}\n").encode()


def local_source(root):
    source, universe = create_source(root)
    with sqlite3.connect(source) as cx:
        cx.executescript("""
            CREATE TABLE history_documents(document_id,source_id,report_period,source_uri,sha256,
                ingested_at,published_at,publication_verified);
            CREATE TABLE history_blobs(sha256,body);
        """)
        body = index_csv()
        sha = hashlib.sha256(body).hexdigest()
        cx.execute("INSERT INTO history_blobs VALUES(?,?)", (sha, body))
        cx.execute("INSERT INTO history_documents VALUES(?,?,?,?,?,?,?,?)", (
            "SYNTHETIC-index", "nse-index-close-observed-v1", "2026-10-06",
            "https://nsearchives.nseindia.com/content/indices/ind_close_all_06102026.csv", sha,
            "2026-10-06T12:00:00+00:00", None, 0))
    cx.close()
    return source, universe

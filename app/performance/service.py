"""Session-aware cohorts, durable nightly catch-up, and descriptive reports."""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from app.performance.repository import PerformanceRepository, strategy_id
from app.performance.provider import OutcomeDataUnavailable

HORIZONS = (1, 5, 10)
EVALUATION_VERSION = "next-open-price-v1"


def returns(stock_entry, stock_exit, benchmark_entry, benchmark_exit):
    prices = (stock_entry, stock_exit, benchmark_entry, benchmark_exit)
    if not all(math.isfinite(p) and p > 0 for p in prices):
        raise ValueError("Prices must be finite and positive")
    stock = 100 * (stock_exit / stock_entry - 1)
    benchmark = 100 * (benchmark_exit / benchmark_entry - 1)
    return stock, benchmark, stock - benchmark


class PerformanceService:
    def __init__(self, config, clock, calendar, provider, repository=None):
        self.config, self.clock, self.calendar, self.provider = config, clock, calendar, provider
        self.repository = repository or PerformanceRepository(config.PERFORMANCE_DATA_DIR)

    def sync(self, conn):
        """Canonical earliest completed-bar capture per ticker/date/strategy."""
        records = conn.execute("""SELECT s.*,sc.timestamp,sc.strategy_context FROM signals s
            JOIN scans sc ON sc.id=s.scan_id WHERE s.market='NSE'
            ORDER BY julianday(sc.timestamp),s.id""").fetchall()
        for row in records:
            exclusion = "partial_session" if row["session_partial"] else ""
            entry, exits = None, {}
            try:
                available = pd.Timestamp(row["timestamp"])
                if available.tzinfo is None:
                    raise ValueError("Missing timezone")
                cal = self.calendar.calendar
                bar = pd.Timestamp(row["bar_date"])
                if not cal.is_session(bar):
                    raise ValueError("Signal date is not an exchange session")
                available = max(available, cal.session_close(bar))
                if not exclusion:
                    session = cal.date_to_session(available.tz_convert(self.calendar.tz).date(), direction="next")
                    if cal.session_open(session) <= available:
                        session = cal.next_session(session)
                    entry = session.date().isoformat()
                    for horizon in HORIZONS:
                        exit_session = cal.session_offset(session, horizon - 1)
                        exits[horizon] = (exit_session.date().isoformat(), cal.session_close(exit_session).isoformat())
            except Exception:
                exclusion = exclusion or "invalid_or_unsupported_session"
                available = row["timestamp"] or "unknown"
            strategy = strategy_id(row["strategy_context"])
            conn.execute("""INSERT OR IGNORE INTO signal_cohorts
                (signal_id,market,ticker,bar_date,available_at,entry_session,strategy,strategy_context,
                 score,volume_ratio,rsi,exclusion) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row["id"], row["market"], row["ticker"], row["bar_date"] or "unknown", str(available),
                 entry, strategy, row["strategy_context"], row["score"], row["volume_ratio"], row["rsi"], exclusion))
            cohort = conn.execute("""SELECT id FROM signal_cohorts
                WHERE market=? AND ticker=? AND bar_date=? AND strategy=? AND exclusion=?""",
                (row["market"],row["ticker"],row["bar_date"] or "unknown",strategy,exclusion)).fetchone()[0]
            for horizon in HORIZONS:
                exit_date, exit_at = exits.get(horizon, (None, None))
                conn.execute("""INSERT OR IGNORE INTO signal_outcomes
                    (cohort_id,horizon,exit_session,exit_at,status,reason,benchmark,price_basis,evaluation_version)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (cohort,horizon,exit_date,exit_at,
                    "excluded" if exclusion else "pending", exclusion or "awaiting_session",
                    self.config.PERFORMANCE_BENCHMARK,self.config.PERFORMANCE_PRICE_BASIS,EVALUATION_VERSION))

    def due_date(self):
        now = self.clock.now().astimezone(ZoneInfo("Asia/Kolkata"))
        target = datetime.combine(now.date(), time.fromisoformat(self.config.PERFORMANCE_NIGHTLY_TIME), now.tzinfo)
        return (now.date() if now >= target else now.date() - timedelta(days=1)).isoformat()

    def run_if_due(self):
        """Called in the existing scheduler's worker thread under its scan lock."""
        if not self.config.PERFORMANCE_ENABLED:
            return False
        now = self.clock.now().astimezone(timezone.utc)
        stamp, due = now.isoformat(), self.due_date()
        cutoff = datetime.combine(datetime.fromisoformat(due).date(),
            time.fromisoformat(self.config.PERFORMANCE_NIGHTLY_TIME),
            ZoneInfo("Asia/Kolkata")).astimezone(timezone.utc).isoformat()
        with self.repository.connection() as conn:
            self.sync(conn)
            job = conn.execute("SELECT * FROM performance_jobs WHERE due_date=?", (due,)).fetchone()
            if job and (job["status"] == "complete" or
                        (job["status"] == "failed" and job["attempts"] >= self.config.PERFORMANCE_MAX_RETRIES) or
                        (job["retry_at"] and datetime.fromisoformat(job["retry_at"]) > now)):
                return False
            conn.execute("""INSERT INTO performance_jobs(due_date,status,started_at,attempts)
                VALUES (?,'running',?,1) ON CONFLICT(due_date) DO UPDATE SET
                status='running',started_at=excluded.started_at,
                attempts=CASE WHEN performance_jobs.status='failed' THEN attempts+1 ELSE 1 END,
                error=NULL""", (due,stamp))
            rows = conn.execute("""SELECT c.*,o.horizon,o.exit_session,o.exit_at FROM signal_cohorts c
                JOIN signal_outcomes o ON o.cohort_id=c.id WHERE o.status IN ('pending','unresolved')
                AND o.benchmark=? AND o.price_basis=? AND o.evaluation_version=?
                AND o.exit_at<=? AND (o.last_attempt IS NULL OR o.last_attempt<?)
                ORDER BY o.last_attempt,c.id,o.horizon""", (self.config.PERFORMANCE_BENCHMARK,
                self.config.PERFORMANCE_PRICE_BASIS,EVALUATION_VERSION,stamp,cutoff)).fetchall()
        ids = list(dict.fromkeys(row["id"] for row in rows))[:self.config.PERFORMANCE_BATCH_SIZE]
        selected = [row for row in rows if row["id"] in ids]
        cache, errors = {}, []
        vintage = stamp
        for cohort_id in ids:
            cohort_rows = [row for row in selected if row["id"] == cohort_id]
            first = cohort_rows[0]
            start = datetime.fromisoformat(first["entry_session"]).date()
            end = max(datetime.fromisoformat(row["exit_session"]).date() for row in cohort_rows)
            benchmark = self.config.PERFORMANCE_BENCHMARK
            try:
                def history(ticker):
                    key = (ticker,start,end)
                    if key not in cache:
                        cache[key] = self.provider.history(ticker,start,end)
                    return cache[key]
                try:
                    stocks = history(first["ticker"])
                except OutcomeDataUnavailable as error:
                    with self.repository.connection() as conn:
                        for row in cohort_rows:
                            self._unresolved(conn,cohort_id,row["horizon"],stamp,"stock_history_unavailable: " + str(error)[:300])
                    continue  # Delisted/unsupported names cannot starve other cohorts.
                index = history(benchmark)
                with self.repository.connection() as conn:
                    for ticker, quotes in ((first["ticker"],stocks),(benchmark,index)):
                        for session, quote in quotes.items():
                            conn.execute("INSERT OR IGNORE INTO performance_prices VALUES (?,?,?,?,?,?,?,?)",
                                (ticker,session,self.config.PERFORMANCE_PRICE_BASIS,vintage,quote["open"],quote["close"],quote.get("dividend",0),quote.get("split",0)))
                    for row in cohort_rows:
                        try:
                            prices = (stocks[first["entry_session"]]["open"], stocks[row["exit_session"]]["close"],
                                      index[first["entry_session"]]["open"], index[row["exit_session"]]["close"])
                            stock, market, excess = returns(*prices)
                            conn.execute("""UPDATE signal_outcomes SET status='resolved',reason=NULL,
                                stock_entry=?,stock_exit=?,benchmark_entry=?,benchmark_exit=?,stock_return=?,
                                benchmark_return=?,excess_return=?,vintage=?,evaluated_at=?,last_attempt=?
                                WHERE cohort_id=? AND horizon=? AND benchmark=? AND price_basis=? AND evaluation_version=?""",
                                (*prices,stock,market,excess,vintage,stamp,stamp,cohort_id,row["horizon"],
                                 benchmark,self.config.PERFORMANCE_PRICE_BASIS,EVALUATION_VERSION))
                        except (KeyError, ValueError) as error:
                            self._unresolved(conn,cohort_id,row["horizon"],stamp,"missing_exact_session_price: " + str(error))
            except Exception as error:
                message = str(error)[:300]
                errors.append(message)
                with self.repository.connection() as conn:
                    for row in cohort_rows:
                        self._unresolved(conn,cohort_id,row["horizon"],stamp,"provider_error: " + message)
                break  # Systemic provider failure stops this batch; nightly backoff retries.
        with self.repository.connection() as conn:
            remaining = conn.execute("""SELECT count(*) FROM signal_outcomes WHERE status IN ('pending','unresolved')
                AND benchmark=? AND price_basis=? AND evaluation_version=?
                AND exit_at<=? AND (last_attempt IS NULL OR last_attempt<?)""",
                (self.config.PERFORMANCE_BENCHMARK,self.config.PERFORMANCE_PRICE_BASIS,
                 EVALUATION_VERSION,stamp,cutoff)).fetchone()[0]
            status = "failed" if errors else ("continuing" if remaining else "complete")
            retry = (now + timedelta(seconds=self.config.PERFORMANCE_RETRY_SEC)).isoformat() if status != "complete" else None
            # Failures are retryable within this night's bounded attempt budget.
            if errors:
                conn.execute("UPDATE signal_outcomes SET last_attempt=NULL WHERE last_attempt=? AND status='unresolved'", (stamp,))
            conn.execute("""UPDATE performance_jobs SET status=?,completed_at=?,retry_at=?,error=?,
                processed=processed+? WHERE due_date=?""", (status,stamp,retry,"; ".join(errors) or None,len(ids),due))
        return True

    def _unresolved(self, conn, cohort, horizon, stamp, reason):
        conn.execute("""UPDATE signal_outcomes SET status='unresolved',reason=?,last_attempt=?
            WHERE cohort_id=? AND horizon=? AND benchmark=? AND price_basis=? AND evaluation_version=?""",
            (reason,stamp,cohort,horizon,self.config.PERFORMANCE_BENCHMARK,
             self.config.PERFORMANCE_PRICE_BASIS,EVALUATION_VERSION))

    def report(self, market="NSE", horizon=5, start=None, end=None, strategy=None):
        if market != "NSE":
            return {"supported": False, "message": "Performance tracking currently supports NSE against Nifty 500."}
        if horizon not in HORIZONS:
            raise ValueError("Horizon must be 1, 5 or 10 sessions")
        with self.repository.connection() as conn:
            self.sync(conn)
            rows = [dict(row) for row in conn.execute("""SELECT c.*,o.* FROM signal_cohorts c
                JOIN signal_outcomes o ON o.cohort_id=c.id WHERE o.horizon=?
                AND o.benchmark=? AND o.price_basis=? AND o.evaluation_version=?
                AND (? IS NULL OR c.bar_date>=?) AND (? IS NULL OR c.bar_date<=?)
                AND (? IS NULL OR c.strategy=?) ORDER BY c.bar_date,c.ticker""",
                (horizon,self.config.PERFORMANCE_BENCHMARK,self.config.PERFORMANCE_PRICE_BASIS,
                 EVALUATION_VERSION,start,start,end,end,strategy,strategy))]
            strategies = [r[0] for r in conn.execute("SELECT DISTINCT strategy FROM signal_cohorts ORDER BY strategy")]
            job = conn.execute("SELECT * FROM performance_jobs ORDER BY due_date DESC LIMIT 1").fetchone()
            raw = conn.execute("SELECT count(*) FROM signals WHERE market='NSE'").fetchone()[0]
            cohorts = conn.execute("SELECT count(*) FROM signal_cohorts").fetchone()[0]
        now = self.clock.now()
        for row in rows:
            row["matured"] = bool(row["exit_at"] and datetime.fromisoformat(row["exit_at"]) <= now)
            if row["status"] == "pending" and row["matured"]:
                row["status"], row["reason"] = "unresolved", "awaiting_evaluation"
        groups = {}
        for field, setting in (("score","PERFORMANCE_SCORE_EDGES"),("volume_ratio","PERFORMANCE_VOLUME_EDGES"),("rsi","PERFORMANCE_RSI_EDGES")):
            edges = [float(x) for x in getattr(self.config,setting).split(",")]
            bounds = [-math.inf,*edges,math.inf]
            buckets = []
            for low, high in zip(bounds,bounds[1:]):
                selected = [r for r in rows if r[field] is not None and math.isfinite(r[field]) and low <= r[field] < high]
                label = f"< {high:g}" if low == -math.inf else (f"≥ {low:g}" if high == math.inf else f"{low:g}–<{high:g}")
                buckets.append({"bucket": label, **self.summarize(selected)})
            buckets.append({"bucket": "Unknown", **self.summarize([r for r in rows if r[field] is None or not math.isfinite(r[field])])})
            groups[field] = buckets
        return {"supported": True, "market": market, "horizon": horizon,
                "benchmark": self.config.PERFORMANCE_BENCHMARK,
                "price_basis": self.config.PERFORMANCE_PRICE_BASIS, "enabled": self.config.PERFORMANCE_ENABLED,
                "nightly_time": self.config.PERFORMANCE_NIGHTLY_TIME, "strategies": strategies,
                "duplicates_removed": max(0,raw-cohorts), "summary": self.summarize(rows),
                "groups": groups, "outcomes": rows, "job": dict(job) if job else None}

    def summarize(self, rows):
        valid = [r for r in rows if r["status"] == "resolved"]
        eligible = sum(r["status"] != "excluded" for r in rows)
        matured = sum(r.get("matured",False) for r in rows)
        hits = sum(r["excess_return"] > 0 for r in valid)
        average = lambda key: statistics.mean(r[key] for r in valid) if valid else None
        return {"eligible": eligible, "matured": matured, "valid": len(valid),
                "pending": sum(r["status"] == "pending" for r in rows),
                "unresolved": sum(r["status"] == "unresolved" for r in rows),
                "excluded": sum(r["status"] == "excluded" for r in rows),
                "coverage": 100 * len(valid)/matured if matured else None,
                "hit_rate": 100 * hits/len(valid) if valid else None,
                "mean_excess": average("excess_return"), "mean_stock": average("stock_return"),
                "mean_benchmark": average("benchmark_return"),
                "median_excess": statistics.median(r["excess_return"] for r in valid) if valid else None,
                "low_sample": len(valid) < self.config.PERFORMANCE_MIN_SAMPLE}

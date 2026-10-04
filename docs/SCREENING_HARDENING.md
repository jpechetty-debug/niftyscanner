# Screening and history hardening — 2026-10-04

## Changes and review findings

1. **Liquidity:** `MIN_AVG_VOLUME` defaults to 100,000 previous-session average shares instead of zero. Thin spikes are filtered normally, before P/E requests. This is an initial configurable guardrail, not a measured optimal threshold or a guarantee of next-open fills.
2. **Loss-making stocks:** `MIN_PE=1` rejects negative, zero and sub-1 P/E by default. Finite negative values remain valid provider data; an explicit negative `MIN_PE` preserves opt-in compatibility. Ranking math is unchanged. Strategy metadata distinguishes the new thresholds from historical ones.
3. **NYSE instruments:** debt, funds and acquisition names are excluded; common/ordinary-share or ADS/ADR descriptions are required. Actual local survivors decreased from **2,294 to 1,819** (475 removed). No remaining survivors match the added Notes/Debentures/Acquisition/Fund terms. Generic `Trust` remains allowed: 91 matching equity descriptions survive, including genuine common-stock REITs. Descriptions are still a heuristic, not authoritative classification.
4. **Intraday projection:** the bounded linear formula remains as specified. UI labels partial-session volume as indicative and quotes as delayed. Partial-session outcomes remain excluded from performance evaluation.
5. **History growth:** JSON snapshots continue updating every successful scan. Runtime SQL history defaults to one first completed post-close capture per market, bar session and strategy, after the configured post-close delay. Optional intraday slots use `HISTORY_INTRADAY_INTERVAL_SEC` (0 disables; 1800 samples half-hour slots). `HISTORY_MODE=all` restores every-scan history. Slot claim and scan/signals commit atomically, including across restarts and competing writers. No existing scans, signals or cohorts are deleted. Low-level direct persistence callers retain append-all compatibility. Legacy rows are not retroactively assigned new slots; the first capture after adopting this policy can add one slot alongside legacy history.
6. **Calendar:** XBOM remains the disclosed NSE proxy. Muhurat and other special NSE sessions are **NOT VERIFIED** and were not replaced with invented schedules.
7. **Other observations:** Performance UI now states that 5/10-session outcomes overlap and statistics have no significance test. Data paths remain relative; README explicitly requires launching from the project root. Authentication is unchanged under the existing localhost default.

## Real data verification

- Fetched the constituent link from the [official Nifty 500 page](https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-500), then downloaded its [constituent CSV](https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv). Local and official lists both have **501 rows**, with **no missing or extra symbols**. The local file has no duplicate symbols. Its TMPV and TMCV symbols therefore match the current downloadable list. The rebalance announcement's effective date was not independently reconciled.
- The three RR rows are BAGMANE, BIRET and EMBASSY. The BE rows are HFCL, MTARTECH and STLTECH. BE means trade-for-trade equities, not REIT units: [NSE series legend](https://www.nseindia.com/static/market-data/legend-of-series). Neither series was removed from the official universe; missing P/E still produces an explicit data failure.
- Audit download SHA-256: `2959bf206239284e145f7aecc65095b18d11556d2642323a70f2d26efe0f5cb3`. Evidence is retained in ignored `logs/universe-audit/`.
- The real `YahooOutcomeProvider` returned **22** valid Open/Close sessions for `^CRSLDX` from September 1 through October 1, matching the proxy calendar's expected session count. Returned opens were positive and finite. This is basic provider sanity, **not** independent reconciliation against official index opens. Evidence: ignored `logs/benchmark-audit/open-check.json`.
- Both live verification window checks deferred on Sunday, October 4. No full Yahoo stock scan was attempted outside the requested windows. Market-hours and post-close feed behavior remain **NOT VERIFIED**.

## Files created / modified

Created: `app/cache/history.py`, `tests/test_screening_hardening.py`, this report.

Modified: `.env.example`, `README.md`, `docs/SPEC.md`, `app/cache/persistence.py`, `app/core/config.py`, `app/core/filters.py`, `app/services/state.py`, `app/universe/nyse.py`, `ui/app.py`, `ui/performance.py`, `tests/test_config.py`, `tests/test_scanner_service.py`, `tests/fixtures/otherlisted_synthetic.txt`.

The synthetic integration scenario now explicitly chooses its original 20 P/E cap and disabled liquidity floor; dedicated new tests cover the stronger defaults. The synthetic class-B description explicitly identifies common stock. SPEC defaults were updated to reflect the user's requested changes.

## Commands and results

```powershell
$taskTmp = Join-Path (Get-Location).Path ('logs/hardening-tests-' + [guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp $taskTmp
.venv/Scripts/python.exe -m compileall -q app ui tests/test_screening_hardening.py
git diff --check
.venv/Scripts/python.exe -m scripts.live_verify --mode market-hours --check-only
.venv/Scripts/python.exe -m scripts.live_verify --mode post-close --check-only
```

Actual final pytest output: **166 passed, 1 warning in 7.87s**. Warning: Starlette's deprecated AnyIO alias. Compile and diff checks succeeded (Git reported LF/CRLF normalization notices). The two live checks printed `in_window=False` and `Deferred: no Yahoo requests made outside the requested window.`

Additional executed checks: CodeGraph source exploration, `git status --short` / `git diff --stat`, Python real-universe audits and narrow benchmark fetch, SQLite backup/integrity probes, verified local preview process reload, and HTTP probes. `/api/status`, `/api/results?market=NSE`, `/api/performance?market=NSE&horizon=5` all returned **200** after reload. `PRAGMA integrity_check` returned **ok**. Raw signals remained **54 NSE / 39 NYSE**, with **zero synthetic TEST tickers** in real history; the performance report retained **18 pending / 0 resolved**. The additive slot table exists. Backup: ignored `logs/history-before-screening-hardening.db`.

The existing preview's scheduler-disabled configuration was preserved. New screening thresholds apply to subsequent scans; historical results were not rewritten. Tests exercise SQL rollback, restart/concurrent idempotence, latest JSON snapshots despite skipped SQL writes, strategy separation, market-specific closes, intraday sampling, and startup configuration validation.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0: phase discipline | Done | One hardening phase; SPEC read before implementation |
| 1/5: screening/config | Done | User-requested defaults updated; overrides remain available |
| 2: data integrity | Done | Real constituent comparison; isolated synthetic regressions |
| 6: universes | Done | Configurable NYSE descriptions; official NSE file retained |
| 9: volume projection | Done | Formula retained; indicative limitation clarified |
| 10/11/12: filtering/ranking | Done | Normal filter counts preserved; scoring unchanged |
| 14: persistence | Done | Atomic snapshots and SQL slots; no historical pruning |
| 15/17: scheduler/API | Done | Worker persistence and localhost behavior retained |
| 16: special NSE sessions | Partial | Proxy remains; special-session accuracy unverified |
| 18/20: UI/testing | Done | Clear labels; 166 executed tests pass |
| Live market validation | Partial | Window checks executed; full scans await a trading session |

## STATE SUMMARY

```text
Phase: screening/history hardening complete; live-window validation pending.
app/core/config.py: stronger defaults; history policy and NYSE regex settings.
app/core/filters.py: inclusive P/E lower-bound documentation.
app/universe/nyse.py: exclusions plus required equity description.
app/cache/history.py: history_capture_key(payload, config, calendar) -> tuple | None.
app/cache/persistence.py: save_last_scan(..., append_history=True, history_key=None).
app/services/state.py: runtime selects canonical/all history policy.
ui/app.py: indicative partial-volume labels.
ui/performance.py: overlapping/descriptive statistics disclosure.
tests/test_screening_hardening.py: 18 isolated synthetic regression cases.
tests/test_config.py: stronger default assertions.
tests/test_scanner_service.py: explicit original fixture scenario.
tests/fixtures/otherlisted_synthetic.txt: class-B common-stock description.
.env.example, README.md, docs/SPEC.md: updated config and operating contract.
docs/SCREENING_HARDENING.md: findings, evidence and limits.
Keys added: HISTORY_MODE, HISTORY_INTRADAY_INTERVAL_SEC, NYSE_REQUIRE_NAME_PATTERN.
Default MIN_AVG_VOLUME=100000; MIN_PE=1; history canonical; intraday sampling off.
166 passed; compile/diff checks pass; local API 200; real database integrity ok.
NYSE 1819 survivors; NSE official symbol comparison exact at 501 rows.
Existing raw signals 54 NSE / 39 NYSE preserved; preview scheduler remains disabled.
Special NSE sessions, independent official opens, full live scans not verified.
Relative paths still require launching from the project root.
Next step after CONTINUE: live_verify during and after an NSE trading session.
NSE context enrichment remains queued after validation.
```

# Review corrections — 2026-10-04

This corrective phase replaces the overly restrictive NYSE wording rule and shared share-count floor, and distinguishes confirmed non-positive earnings from unavailable P/E. The user's corrections authorize these SPEC updates. No next phase was started.

## Behavior and assumptions

- NYSE descriptions no longer need common/ordinary-share or ADS wording. Exclusions cover debt, acquisition vehicles, funds, municipal(s), opportunity/opportunities, term/income trusts and targeted fund descriptions. The audit also caught abbreviated STRATS/CorTS debt and capital trusts, plus fund descriptions without the literal word Trust. Generic Trust and Beneficial Interest remain allowed. BlackRock Inc. and Franklin BSP Realty Trust are retained alongside AMH and COPT Defense Properties (CDP).
- Against the same supplied otherlisted.txt and the prior committed loader: **1,819 → 1,860** survivors; **87 restored / 46 newly excluded**. AAP, AME, CCK, CHE, BBWI, EQNR, FNV and the named Brookfield entities are restored. This is a description heuristic, not proof that all remaining securities are operating-company common stock. Broad keyword false positives and unrecognized closed-end funds remain possible; authoritative instrument classification was not added.
- Liquidity uses mean(previous completed sessions' Close × observed Volume). Latest bars and intraday projected volumes are excluded. Defaults are **₹10,000,000 NSE / US$1,000,000 NYSE**; `MIN_AVG_VOLUME=0` leaves the optional share floor off. Zero disables either market's value floor. These are configurable starting guardrails, not empirically optimized thresholds or guaranteed fills. Adjusted Yahoo closes make this a proxy, not exchange VWAP turnover. Older/custom IndicatorResult callers fall back to avg volume × latest price.
- Missing/null trailingPE with finite reported trailingEps ≤0 now yields an internal NonPositiveEarnings marker, counted under filtered_pe. It is cached with the normal P/E TTL and causes no breaker failure. Missing P/E with unknown/invalid/positive EPS stays MISSING_PE; invalid reported P/E stays INVALID_PE. No earnings or P/E is invented. Numeric P/E filtering/ranking remains unchanged. New strategy metadata records both liquidity floors; historical signals and strategy contexts are preserved.
- Actual narrow Yahoo probes returned absent trailingPE and trailingEps **−1.06 for AMC / −0.27 for NIO**. This confirms the reported behavior for those two observations, not every company. Each symbol was present in the supplied universe.

## Inspectable evidence

[Portable evidence archive](evidence/review-evidence-2026-10-04.zip) includes a consistent SQLite backup, official and local constituent CSVs, source/hash metadata, all 22 benchmark sessions, P/E/EPS observations, NYSE changes, test output and local HTTP results. Extract verify.py and run `python verify.py --verify <archive.zip>` without network or project dependencies.

The verifier checks every manifest hash, recomputes constituent equality and duplicate checks, validates recorded benchmark coverage and positive finite Open/Close, and queries database integrity/counts. Hashes detect changed archive contents; they do not authenticate source data. The official CSV is the recorded October 4 download from https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv, SHA-256 `2959bf206239284e145f7aecc65095b18d11556d2642323a70f2d26efe0f5cb3`. It matches the local **501 symbols**, with no duplicates. Effective rebalance dates were not independently reconciled.

The refreshed real Yahoo ^CRSLDX probe returned **22 sessions**, September 1–October 1 inclusive, matching the recorded XBOM proxy schedule. Open and Close are finite and positive. This is provider sanity, **not independent official-index reconciliation**.

The database backup reports integrity **ok**, **54 NSE / 39 NYSE signals**, **zero TEST tickers**, **18 cohorts / 54 pending outcome rows**. Pending outcomes are not measured hit rates. The API preview was reloaded retaining its disabled scheduler. Status, both market results and NSE performance returned **HTTP 200**. Stored results remain marked stale until a successful new scan; they were not recomputed with corrected filters.

## Files created / modified

Created: `app/core/fundamentals.py`, `scripts/review_evidence.py`, `tests/test_review_corrections.py`, `tests/test_review_evidence.py`, this report and `docs/evidence/review-evidence-2026-10-04.zip`.

Modified: `.env.example`, `README.md`, `docs/SPEC.md`, `docs/SCREENING_HARDENING.md`, `app/core/config.py`, `app/core/filters.py`, `app/core/indicators.py`, `app/core/interfaces.py`, `app/cache/pe_cache.py`, `app/providers/yfinance_provider.py`, `app/services/scanner.py`, `app/performance/repository.py`, `app/universe/nyse.py`, `tests/test_config.py`, `tests/test_screening_hardening.py`.

## Executed commands and actual results

```powershell
$taskTmp=Join-Path (Get-Location).Path ('logs/review-tests-'+[guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp $taskTmp | Tee-Object -FilePath logs/review-audit/pytest.txt
.venv/Scripts/python.exe -m compileall -q app scripts tests/test_review_corrections.py tests/test_review_evidence.py
git diff --check
.venv/Scripts/python.exe -m scripts.live_verify --mode market-hours --check-only
.venv/Scripts/python.exe -m scripts.live_verify --mode post-close --check-only
.venv/Scripts/python.exe -m scripts.review_evidence
.venv/Scripts/python.exe -m scripts.review_evidence --verify docs/evidence/review-evidence-2026-10-04.zip
```

Actual pytest output:

```text
190 passed, 1 warning in 7.42s
```

Warning: Starlette's deprecated AnyIO BlockingPortal alias. Compile and whitespace checks succeeded; Git printed line-ending normalization notices. Both live-window checks printed `in_window=False` and `Deferred: no Yahoo requests made outside the requested window.`

Also executed: CodeGraph exploration, read-only Python audits comparing current and prior committed NYSE loaders, two Ticker.info probes, one YahooOutcomeProvider benchmark fetch, read-only SQLite queries/backup, verified process inspection/restart and HTTP probes. Archive verification was also run from its extracted standalone verifier outside the project working directory.

**VERIFIED:** 190 executed tests, named company/REIT retention, native-currency filter boundaries, completed-bar liquidity calculation, missing-PE/EPS classification and cache behavior, portable evidence hashes/counts, real narrow probes and local API responses.

**NOT VERIFIED:** complete instrument classification, statistically optimal floors, official index opens, special NSE sessions, latest rebalance effective date, full market-hours/post-close scans and actual nightly scheduling in the scheduler-disabled preview. XBOM proxy, relative paths and bounded linear intraday projection remain disclosed limitations. Settings/refresh authentication remains unchanged under localhost binding.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0: phase discipline | Done | SPEC read; one corrective phase; commands/results recorded |
| 1/5/9: liquidity/config | Done | User-authorized currency floors; configurable completed-bar proxy |
| 2: data integrity | Done | Real evidence archived; tests isolated and labelled SYNTHETIC |
| 6: NYSE universe | Done | Mandatory wording removed; configurable exclusions with stated limits |
| 7/10/11/13/14: provider/filter/cache | Done | Typed earnings evidence; normal filtering; TTL/breaker behavior preserved |
| 12: ranking | Done | Formula unchanged; new thresholds recorded in strategy metadata |
| 15/17: scheduler/API | Done | Nonblocking architecture preserved; preview HTTP checks pass |
| 16: NSE sessions | Partial | Existing proxy; special sessions unverified |
| 20/22: tests/evidence | Done | 190 executed tests; standalone offline evidence verification |
| Full live validation | Partial | Sunday checks deferred; narrow observed probes only |

## STATE SUMMARY

```text
Phase: review corrections complete; next phase not started.
app/core/config.py: currency floors, optional share floor, revised NYSE exclusions.
app/core/fundamentals.py: NonPositiveEarnings(trailing_eps); confirmed_nonpositive_earnings(info).
app/core/indicators.py: IndicatorResult.avg_traded_value_20d from completed paired bars.
app/core/filters.py: apply_stage1_filters(indicator, config, tracker, market='NSE').
app/core/interfaces.py: fetch_pe_batch returns float or NonPositiveEarnings values.
app/cache/pe_cache.py: typed earnings evidence shares P/E TTL.
app/providers/yfinance_provider.py: classify absent/null P/E using reported EPS.
app/services/scanner.py: market liquidity selection; earnings marker counted as filtered_pe.
app/performance/repository.py: strategy_context records both currency floors.
app/universe/nyse.py: no required share-class wording; targeted exclusions.
scripts/review_evidence.py: build_archive(root, output), verify_archive(path), database_summary(path).
tests/test_review_corrections.py: SYNTHETIC universe/liquidity/EPS/cache/funnel regressions.
tests/test_review_evidence.py: SYNTHETIC verification/tamper/inconsistent-claim regressions.
tests/test_config.py: assert new defaults.
tests/test_screening_hardening.py: align earlier tests with removed require rule.
.env.example: new floors; retired require setting removed.
README.md, docs/SPEC.md: updated contracts and limitations.
docs/SCREENING_HARDENING.md: historical report linked to corrections.
docs/REVIEW_CORRECTIONS.md: results, commands and verification limits.
docs/evidence/review-evidence-2026-10-04.zip: inspectable real evidence and database snapshot.
Keys added: MIN_AVG_TRADED_VALUE_NSE=10000000; MIN_AVG_TRADED_VALUE_NYSE=1000000.
MIN_AVG_VOLUME=0; MIN_PE=1; retired NYSE_REQUIRE_NAME_PATTERN ignored.
190 tests passed; compile/diff checks succeeded; local API checks HTTP 200.
NYSE 1860 survivors; 87 restored, 46 newly excluded vs prior 1819.
501 constituent symbols match recorded official download; benchmark 22 valid sessions.
Raw history 54 NSE / 39 NYSE preserved; 18 cohorts, 54 pending outcomes.
Preview scheduler remains disabled; old results stale until a successful scan.
Assumptions: initial liquidity guardrails; close-based proxy; description heuristic.
Known limits: XBOM, relative paths, linear projection, unverified official opens/live windows.
Next step after CONTINUE: re-read SPEC and run live_verify in actual trading windows.
```

# Four-hour Breakout cache prerequisite

Implemented 22 October 2026, **B0 only**. This is the user's requested cache-first
foundation for integration, not a completed four-hour screener. Master spec section
36 and `docs/BREAKOUT_SHARED_INFRASTRUCTURE.md` define the authorized boundaries.

## Result

`BarCacheKey` isolates histories by provider, ticker, interval and adjustment
policy. The existing string API maps to yfinance / 1d / auto_adjust, and the
production daily provider now passes that explicit key to every cache operation.
Hourly, raw or different-source entries cannot cause the daily provider to choose
its five-day delta path, receive those bars, or evict those entries on adjustment.
No changes were made to MarketDataProvider's public daily/P/E signatures.

Intraday merges keep all supplied rows unless an explicit positive `max_rows`
bound is passed. They no longer inherit the daily 100-row merge policy. Daily
merges retain their legacy floor and original full-history length while also
preserving a larger full incoming download merged into a shorter cache. Compatible
aware timezones are converted to the cached timezone before intraday concatenation,
preserving an aware DatetimeIndex and the actual instants.

Daily consistency remains Close-only, with the existing tolerance and newest-row
exclusion. Intraday consistency compares complete finite OHLCV exactly on shared
timestamps. It conservatively checks every shared bar unless the caller supplies
an aware `completed_through` bar-start watermark; only starts at or before that
watermark are compared. Duplicate/naive/invalid indexes, missing fields, nonfinite
values and nonoverlapping histories fail the intraday comparison. The cache never
guesses bar finality from elapsed time or sample count.

The caller still owns validation, full-refetch decisions, session/grace-aware
completion, requested-history coverage and retention/warm-up bounds. Cache labels
describe requested transformations; they do not supply verified price-basis,
publication or historical identity evidence. No disk/schema migration or provider
request occurred during this phase.

## Integration reconciliation

The October 5 plan was reconciled with the current daily Trade Ideas implementation
and the user's latest cache-first instruction. B0 precedes the existing separately
continued B1–B6 phases. Four-hour Breakout keeps its own eight-rule result type,
strategy/config/implementation identity and future validation cohorts. Daily Plan,
S3 ledgers and S4/S6 records do not become four-hour evidence.

Subsequent phases reuse provider extraction/retries/counters, chart presentation
primitives, operations surfaces and the existing scheduler lock. They must preserve
daily controls and bind four-hour chart annotations to their actual scan, ticker,
interval and basis. No second scheduler or implicit chart-triggered download is
proposed. The complete-only four-hour policy and eight-rule boundaries remain in
`docs/BREAKOUT_SCREENER_PLAN.md`; hourly finality/coverage/currency and separate
execution/validation evidence remain unverified release gates.

## Files created / modified in this phase

| File | Purpose |
|---|---|
| `app/cache/bar_cache.py` | Immutable cache identity, isolated operations, intraday retention/overlap/timezone handling |
| `app/providers/yfinance_provider.py` | Explicit legacy daily key on all cache paths |
| `tests/fixtures/bar_cache.py` | Labelled SYNTHETIC OHLCV fixture, fictitious TEST1.NS only |
| `tests/test_bar_cache.py` | Cache identity, retention, completion, timezone and mocked provider regressions |
| `docs/SPEC.md` | Section 36, authorized cache-first phase |
| `docs/BREAKOUT_SCREENER_PLAN.md` | Reconcile B0 and shared provider/chart/operations/separate validation mapping |
| `docs/BREAKOUT_SHARED_INFRASTRUCTURE.md` | Current cache contract and proposed later integration boundaries |
| `docs/BREAKOUT_CACHE_IMPLEMENTATION.md` | This implementation/verification/handoff report |
| `README.md` | Explicit foundation status and references |

All earlier P1 explanation, verification-reuse and chart changes remain in the
working tree and were preserved. No dependency, environment key, database, API
response, scheduler task, UI view, real account or strategy record was added.

## Commands and real test output

Read the complete master spec, AGENTS, original attachment, saved breakout plan
and graph review. Used the existing Graphify graph plus current CodeGraph source;
no indexing/rebuild or semantic extraction. Graphify vocabulary expansion:
`cache bars interval provider download daily history chart operations`. The broad
DFS was capped; CodeGraph identified current cache/provider callers and tests.
Graph-query/model token accounting is unavailable; no token savings or monetary
cost claim is made.

```powershell
graphify reflect --if-stale
graphify query 'cache bars interval provider download daily history chart operations' --dfs --budget 1600
.venv/Scripts/python.exe -m pytest tests/test_bar_cache.py tests/test_providers.py tests/test_regressions.py tests/test_repairs.py -q -p no:cacheprovider --basetemp logs/interval-cache-focused/pytest --tb=short -o faulthandler_timeout=60
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp logs/interval-cache-full/pytest --tb=short -o faulthandler_timeout=60
git diff --check
```

TEMP/TMP used the corresponding workspace-local log directory. Completed offline
test runs used approved execution outside Windows sandbox restrictions, with
Yahoo mocked and live tests excluded. No market-data downloads, live scan,
browser preview, imports, workers, alerts or broker calls were performed.

| Execution | Actual result |
|---|---|
| Initial focused cache/provider regression | `3 failed, 93 passed, 1 warning in 20.15s` |
| Cause/correction | Three new frame assertions required frequency metadata retained by Pandas merges; assertions now compare rows/values/index while allowing dropped frequency metadata |
| Additional observed correction | UTC/IST concatenation produced an object Index; intraday merges now preserve the cached timezone and a subsequent consistency check succeeds |
| Corrected full offline suite | **`560 passed, 1 warning in 241.91s (0:04:01)`**, exit 0; includes 42 new cache cases |

The new cases exercise all four key dimensions; scoped merge/eviction and global
clear; frozen/nonblank keys; string compatibility; 1,000 hourly rows; explicit
retention and invalid bounds; larger full-history preservation; each OHLCV field
including the latest bar; explicit completion/timezone/Clock behavior; invalid or
missing overlap; equivalent timezone merges; daily cold/warm selection and request
counts; adjustment refetch isolation from hourly/raw caches.

## Assumptions and VERIFIED / NOT VERIFIED

Assumption: preserve existing daily callers by assigning ticker strings to their
old yfinance daily auto-adjust namespace. All nonlegacy callers must pass an
explicit key. Identity strings are exact; no unverified provider/interval/policy
aliases are guessed. Intraday consistency is deliberately exact, while the old
daily tolerance remains unchanged.

VERIFIED: all 560 offline tests passed, including 42 new identity/retention/
overlap/provider cases and the existing daily/replay/validation/UI regressions.
The only warning is the existing Starlette TestClient use of AnyIO's deprecated
BlockingPortal alias. `git diff --check` passed with only existing CRLF conversion
notices. The plan/contract make the remaining integration boundaries explicit
without enabling the four-hour strategy.

NOT VERIFIED: real hourly Yahoo timestamps/finality/history coverage, market-cap
currency, complete four-hour aggregation, the eight-rule implementation, live
four-hour charts/operations or a four-hour edge. B1–B6 remain pending. No latency,
request-count reduction, profitability, deployment or full integration claim is
made. No code was committed or pushed.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0, 22 — phase/report discipline | Done | One cache-first prerequisite; report and handoff; stop afterward |
| 2, 3, 5 — integrity/stack/config | Done | Synthetic tests isolated; no package/config/strategy threshold changes |
| 7, 8 — providers/data | Done | Legacy signatures preserved; explicit daily identity and same overlap policy |
| 14 — cache | Done | Key isolation, scoped eviction, retention and larger-history preservation |
| 15 — scheduler | Not in this phase | No task, lock or enabling changes |
| 17, 18 — API/UI | Not in this phase | No routes, response or UI changes |
| 20 — tests | Done | 560 offline tests passed, including 42 new cache cases |
| 26–35 — daily research/validation | Done | Existing behaviors/stores retained; full regressions passed |
| 36 — shared cache foundation | Done | Implemented cache/provider prerequisite; future contracts labelled proposals |

## STATE SUMMARY

```text
Phase completed: B0 interval-aware bar-cache prerequisite for four-hour integration.
app/cache/bar_cache.py: provider/ticker/interval/adjustment cache identity and retention/consistency.
app/providers/yfinance_provider.py: explicit daily keys across lookup/refetch/merge.
tests/fixtures/bar_cache.py: SYNTHETIC OHLCV helper; tests/test_bar_cache.py: regressions.
docs/SPEC.md: section 36; README.md: foundation status/reference.
docs/BREAKOUT_SCREENER_PLAN.md: reconcile B0 before separately gated B1–B6.
docs/BREAKOUT_SHARED_INFRASTRUCTURE.md: current contract and future reuse boundaries.
docs/BREAKOUT_CACHE_IMPLEMENTATION.md: implementation/evidence/handoff.
Public: BarCacheKey(ticker, provider='yfinance', interval='1d', adjustment_policy='auto_adjust').
get_bars(ticker: str | BarCacheKey) -> DataFrame | None.
set_bars(ticker, df, *, max_rows: int | None=None) -> None.
update_bars(ticker, new_df, *, max_rows: int | None=None) -> DataFrame.
is_consistent(ticker, new_df, rel_tol=.002, *, completed_through: datetime | None=None) -> bool.
invalidate(ticker: str | BarCacheKey) -> None; clear() -> None.
Config/dependencies/database schema added: none.
Decision: legacy strings mean daily Yahoo auto_adjust; intraday identity must be explicit.
Intraday retention belongs to the adapter; no daily 100-row inheritance.
Watermark names the last completed bar start; cache itself does not establish finality.
Four-hour rules/cohorts remain distinct from daily breakout/pullback/composite.
Known limitation: four-hour feature is not implemented or enabled; hourly release gates unverified.
Tests: 560 passed, 1 existing dependency warning in 241.91s; diff check passed.
Prior P1 edits preserved; no source import, live scan, commit or deployment.
Next: await CONTINUE, reread docs/SPEC.md, complete B1 specification/config/models only.
```

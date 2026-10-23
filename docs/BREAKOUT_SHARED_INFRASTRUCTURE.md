# Four-hour Breakout: shared infrastructure contract

Authorized cache-first integration prerequisite, 22 October 2026. The saved
5 October plan predates daily Trade Ideas S1–S6. The user's current instruction
prioritizes interval-safe cache identity before introducing hourly histories.
This is one implementation phase, not authorization to run all B1–B6 phases.

## This phase: interval-aware cache foundation

Use an immutable identity containing provider, ticker, interval and adjustment
policy for every bar-cache operation. Existing ticker-only calls mean the legacy
yfinance / 1d / auto_adjust namespace. The production daily provider selects that
identity explicitly. A label for adjustment policy records the requested provider
transformation; it is not verified raw/split/dividend evidence for swing research.
Do not put imported unknown-basis observations into Yahoo's adjusted namespace.

Retain the old daily merge behavior, allowing a larger incoming full history to
survive a merge into a shorter cache. Intraday merges must not inherit the daily
100-row retention floor/cap. They keep all supplied rows unless a positive
caller-specified max_rows bound is supplied. Future adapters must choose a bound
sufficient for their configured period/warm-up and apply it on each write.

Intraday overlap checks compare full OHLCV exactly across shared timestamps;
require aware, unique timestamp indexes and complete numeric finite OHLCV for
the compared rows. Without a completion watermark, compare all shared timestamps
conservatively. With completed_through, compare only bar-start timestamps at or
before the caller's aware last-completed bar start. The future provider owns that
session/interval/publication-grace calculation. No overlap forces a full refetch.
This cache check is not proof of market-bar completeness or price-basis provenance.
Daily callers retain their existing Close-only tolerance and newest-row exclusion.

Key-specific invalidation never evicts another interval/provider/policy. clear()
explicitly clears the whole in-memory cache. There is no disk/schema migration,
new provider download, strategy, route, UI control, timer or feature enablement.

## Shared provider contract for subsequent phases (proposal, not runtime API)

Keep MarketDataProvider.download_bars(tickers) and fetch_pe_batch(tickers)
compatible. A supplemental capability should accept an explicit bar request with
ticker, provider, interval, adjustment policy, requested history and as-of instant.
Use the same identity on cache read, consistency check, invalidation and merge;
select full versus delta periods without changing that identity. Keep fetch
timestamps, attempts/failures, basis uncertainty and completed-bar cutoffs explicit.
A cache hit is not proof of sufficient requested history: check time-span and
warm-up coverage before choosing a delta request.

Hourly adapters use the existing provider's extraction, bounded concurrency,
backoff and breaker utilities. They must not derive an hourly history from daily
cache presence or truncate hours with a daily rule. Market-cap access is a separate
typed capability with currency/fetch time and field-specific validation/TTL; it
does not manufacture P/E or replace missing benchmark/basis evidence.

## Integration boundaries retained for B1–B6

- Four-hour Breakout is its own versioned eight-rule hypothesis and result type;
  daily breakout/pullback and composite screening rules, ranking and histories
  retain their current meanings. Do not add FOUR_HOUR plans to S3/S4 daily schemas
  merely to reuse their UI or registry. Separate cohorts/manifests/config and
  implementation hashes prevent cross-timeframe validation/promotion reuse.
- The user-selected complete-only policy remains: session-open-anchored groups of
  four completed contiguous hourly bars, no cross-session grouping or short tail.
  Exact calendar hours, publication grace, missing expected windows and daily-as-of
  lookahead checks belong to B2/B3. No candle construction is implemented here.
- Share chart presentation primitives and recorded-evidence guards, not daily
  resistance/ATR semantics. Four-hour charts need body-based consolidation and
  timestamped candles captured for the selected scan. An immutable scan/strategy/
  ticker/interval/basis identity must bind chart annotations to results.
- Share the existing operations surface and global scheduler lock. Controls/status
  must identify the strategy so a daily replay/pause/alert command cannot silently
  target four-hour research. No second scheduler, implicit downloads on chart reads,
  automatic account funding, default alerts or broker execution.
- Separate four-hour validation requires an executable entry convention, frozen
  costs, publication/identity evidence and observed outcomes. Current market cap
  cannot serve as historical point-in-time proof. Daily validation records and
  prospective captures remain unchanged and cannot attest a four-hour edge.

The original eight rules and inclusive/strict boundaries remain proposed as written
in docs/BREAKOUT_SCREENER_PLAN.md. In particular preserve absolute body size, the
numeric INR market-cap floor, signed high distances and the unsimplified OR rule.
Freeze these in B1 before their implementation. Real hourly coverage/finality,
currency and special-session evidence remain release gates, not verified facts.

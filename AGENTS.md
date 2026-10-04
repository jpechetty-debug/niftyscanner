# Agent Operating Rules & Constraints

## 1. Operating Discipline
- Re-read docs/SPEC.md before starting every phase. (In chat mode, ask the user to re-paste the spec if it is no longer in context.)
- Complete exactly one phase at a time. Never proceed to the next phase without explicit `CONTINUE`.
- End each phase with: files created/modified, commands executed, test results, assumptions, VERIFIED vs NOT VERIFIED, SPEC COMPLIANCE table, and STATE SUMMARY block (<= 40 lines).
- Never claim a test passed unless you executed it. Show real output in agent mode.

## 2. Data Integrity & Fabrication Guardrails
- Never fabricate market data, stock universes, or index constituents for real runs.
- If a required universe file is missing, stop and request it from the user. Never invent tickers.
- Exception: `tests/fixtures/` may hold SYNTHETIC OHLCV data, universe files, and P/E values for fictitious tickers (e.g. `TEST1.NS`), each labelled SYNTHETIC.
- `--offline` and `FakeProvider` use only these synthetic fixtures, print a `SYNTHETIC DATA` banner, and are never served by the API or UI.

## 3. Data Source Limitations (yfinance)
- yfinance is unofficial and intended for personal use only.
- yfinance data is delayed and not real-time.
- Chunking reduces overhead, not request count. Be mindful of rate limits and exponential backoff.

## 4. Market Hours & session_partial
- `session_partial = (latest bar date == exchange-local today) AND (market currently open)`.
- When `session_partial` is true, current-session volume is incomplete; partial-session volume ratios use a bounded linear projection and may overestimate or underestimate closing volume. This field is explicitly exposed in result payloads.

## 5. Architectural Invariants
- Local-first, single-user, bound to `127.0.0.1` by default.
- All configuration driven by environment variables / `.env` with fallbacks. No hardcoded thresholds in code.
- Swappable providers via `MarketDataProvider` protocol; time abstraction via injected `Clock`.
- Strict separation of Filtered Out (normal business logic filter) vs Failed (data issue / network issue).
- FastAPI event loop must remain non-blocking (`asyncio.to_thread` for yfinance calls). UI communicates strictly through HTTP API (`API_BASE_URL`).

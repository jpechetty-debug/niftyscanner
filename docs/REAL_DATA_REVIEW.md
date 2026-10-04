# Real-Data Review Analysis (October 2026 Session)

## Triage Summary

| # | Finding | Severity | Action | Status |
|---|---------|----------|--------|--------|
| 1 | DUMMYHEG placeholder in universe | 🔴 Bug | Drop symbols starting with `DUMMY` in NSE loader | **Done** |
| 2 | Series RR (REIT units) can't pass P/E | 🟡 Improvement | Skip series `RR` in NSE loader | **Done** |
| 3 | Missing 15 Jan 2026 holiday (XBOM proxy) | 🟢 Info | Document upstream `exchange_calendars` limitation, past-only | **Documented** |
| 4 | Early-session volume suppression | 🟢 Design | Document deliberate bound per spec §9 | **Documented** |
| 5 | Aggressive scan rate (~96s effective) | 🟡 Config | Default `REFRESH_INTERVAL_SEC=300` in config and `.env.example` | **Done** |
| 6 | Unbuyable signals (circuit limits) | 🟡 Improvement | Flag day-move % near circuit bands (`circuit_risk`) | **Backlog** |
| 7 | No cost model (STT, stamp duty, slippage) | 🟢 Info | Document tracker returns are gross | **Documented** |
| 8 | P/E single-source risk (Yahoo) | 🟢 Info | Document recommendation for manual spot-check | **Documented** |

---

## 🔴 Immediate Fixes Completed

### 1. Drop `DUMMY` placeholder symbols from NSE universe
- **Problem:** `data/nifty500.csv` contains `DUMMYHEG`, a placeholder row in the official Nifty 500 CSV. It previously failed every scan as "delisted", inflating failure counts.
- **Resolution in `app/universe/nse.py`:** Automatically drop any symbol starting with `DUMMY` during loading.

### 2. Skip Series RR (REIT/InvIT units) from NSE universe
- **Problem:** REIT units (`BAGMANE`, `BIRET`, `EMBASSY`) are series `RR`. They have no trailing equity P/E and can never pass Stage 2.
- **Resolution in `app/universe/nse.py`:** Filter out rows where `Series == 'RR'`. Equity trade-for-trade stocks (series `BE`) remain included as designed.

### 3. Default `REFRESH_INTERVAL_SEC` to 300
- **Problem:** Default of 60s yielded ~230 scans and ~125k ticker-requests per session, creating potential yfinance rate limit risks.
- **Resolution in `app/core/config.py` & `.env.example`:** Changed default `REFRESH_INTERVAL_SEC` to 300s (~75 scans / ~40k requests per session).

---

## 🟢 Documented & Acknowledged Findings

### Calendar: 15 Jan 2026 missing from XBOM proxy
The `exchange_calendars` XBOM calendar lists 15 weekday holidays for 2026; NSE's circular lists 16. The missing one is 15 Jan 2026 (Maharashtra municipal elections). This affects only historical past data; all upcoming closures match. Muhurat trading (Sun 8 Nov) appears as a closed day to the app, which is the safe default.

### Early-session volume suppression is by design
The projection cap of 4× until ~10:49 IST means a stock needs ~37.5% of its 20-day average volume to pass the 1.5× threshold in the early session. Morning breakouts are mostly missed and only appear later. This is a deliberate bound per spec §9.

### P/E is a single third-party source
Yahoo Finance is the sole P/E source. Spot-checking against NSE or screener.in is recommended before executing trades.

### Gross returns, no cost model
The performance tracker reports gross price returns between entry and exit horizons. Delivery trades in India carry STT, stamp duty, and slippage, which is material at 1-day and 5-day horizons.

---

## 🟡 Backlog Item: Circuit-Limit Flagging

Signals with very high volume ratios (e.g. TARIL at 69×, LEMONTREE at 14.6×) often indicate news or block-deal events. The stock may be locked at its circuit limit at the next open, making the assumed open-price entry unrealistic.
**Proposal:** Record the day's percentage move and flag any near NSE's 2/5/10/20% circuit bands in `ScanResultItem`.

# Real-Data Review Analysis (October 2026 Session)

## Triage Summary

| # | Finding | Severity | Action | Status |
|---|---------|----------|--------|--------|
| 1 | DUMMYHEG placeholder in universe | 🔴 Bug | Drop symbols starting with `DUMMY` in NSE loader | **Done** |
| 2 | Series RR (REIT units) can't pass P/E | 🟡 Improvement | Skip series `RR` in NSE loader | **Done** |
| 3 | Missing 15 Jan 2026 holiday (XBOM proxy) | 🟢 Info | Add confirmed NSE closure override | **Implemented** |
| 4 | Early-session volume suppression | 🟢 Design | Document deliberate bound per spec §9 | **Documented** |
| 5 | Aggressive scan rate (~96s effective) | 🟡 Config | Default `REFRESH_INTERVAL_SEC=300` in config and `.env.example` | **Done** |
| 6 | Unbuyable signals (circuit limits) | 🟡 Improvement | Add indicative/reference band proximity warnings | **Implemented; actual live bands unverified** |
| 7 | No cost model (STT, stamp duty, slippage) | 🟢 Info | Gross/estimated net Performance option | **Implemented** |
| 8 | P/E single-source risk (Yahoo) | 🟢 Info | Optional independently checked dated CSV comparison | **Implemented; live reference unavailable** |

---

## 🔴 Immediate Fixes Completed

### 1. Drop `DUMMY` placeholder symbols from NSE universe
- **Problem:** `data/nifty500.csv` contains `DUMMYHEG`, a placeholder row in the official Nifty 500 CSV. It previously failed every scan as "delisted", inflating failure counts.
- **Resolution in `app/universe/nse.py`:** Automatically drop any symbol starting with `DUMMY` during loading.

### 2. Skip Series RR (REIT/InvIT units) from NSE universe
- **Problem:** REIT units (`BAGMANE`, `BIRET`, `EMBASSY`) are series `RR`. They have no trailing equity P/E and can never pass Stage 2.
- **Resolution in `app/universe/nse.py`:** Filter out rows where `Series == 'RR'`. Equity trade-for-trade stocks (series `BE`) remain included as designed.

### 3. Default `REFRESH_INTERVAL_SEC` to 300
- **Problem:** Frequent scanning increases request load. Earlier per-session request estimates were not measurements and depend on adaptive intervals, retries and caching.
- **Resolution in `app/core/config.py` & `.env.example`:** Default `REFRESH_INTERVAL_SEC=300`; saved/runtime preference also updated to 300 in the corrective implementation.

---

## 🟢 Documented & Acknowledged Findings

### Calendar: 15 Jan 2026 missing from XBOM proxy
The XBOM proxy omits the 15 Jan 2026 Maharashtra election closure. `NSE_EXTRA_HOLIDAYS=2026-01-15` now removes it from a separate NSE calendar instance, following [NSE circular 72260](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf). Session-counting and new cohort schedules use this calendar. Future completeness is not established; Muhurat hours remain unsupported. Existing resolved cohorts are not automatically rewritten; archived histories predating the correction need a separate schedule audit.

### Early-session volume suppression is by design
The projection cap of 4× until ~10:49 IST means a stock needs ~37.5% of its 20-day average volume to pass the 1.5× threshold in the early session. Morning breakouts are mostly missed and only appear later. This is a deliberate bound per spec §9.

### P/E is a single third-party source
Yahoo remains the scoring/filtering P/E source. `NSE_REFERENCE_CSV` optionally supplies independently checked dated reference P/E; comparisons expose match/divergent/unavailable status without changing rankings. Automatic NSE quote access returned 403 during verification; no real reference values were fabricated.

### Gross and estimated net returns
The tracker preserves stored gross outcomes and adds an estimated net view with configurable STT, buy stamp duty, brokerage, other charges and slippage. Benchmark returns remain gross. Fixed DP fees, rounding and order-size effects are excluded. See [cost assumptions and verification](FEATURE_COMPLETION.md).

---

## Circuit-Proximity Warnings

Signals with very high volume ratios (e.g. TARIL at 69×, LEMONTREE at 14.6×) often indicate news or block-deal events. The stock may be locked at its circuit limit at the next open, making the assumed open-price entry unrealistic.
**Implemented:** `day_change_pct`, nullable `circuit_risk`, status/reason fields, table/card warnings and CSV export. Common-band proximity is explicitly indicative; supplied dated non-F&O fixed bands support a reference check. F&O dynamic operating ranges are not treated as fixed circuits. Actual next-open availability and live NSE band ingestion remain unverified.

"""Streamlit Frontend Application for Stock Screener.

INVARIANT PER SECTION 18:
The UI talks ONLY to FastAPI via API_BASE_URL (httpx).
It never imports 'app' internals and never triggers scans directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
import sys
from typing import Any, Dict, List, Optional
import pandas as pd
import streamlit as st

# Ensure ui directory is in path for api_client import without app dependencies
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api_client import ScreenerApiClient

st.set_page_config(
    page_title="Stock Screener | Nifty 500 & NYSE",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

api_client = ScreenerApiClient()


# ==============================================================================
# Helper Functions for Client-Side Filtering
# ==============================================================================
def filter_results_dataframe(
    df: pd.DataFrame,
    search_query: str,
    min_rsi: float,
    min_vol_ratio: float,
    max_pe: float,
    partial_only: bool,
) -> pd.DataFrame:
    """Filter results DataFrame based on interactive sidebar controls."""
    if df.empty:
        return df

    filtered = df.copy()

    # Search filter
    if search_query.strip():
        q = search_query.strip().lower()
        mask = (
            filtered["ticker"].astype(str).str.lower().str.contains(q)
            | filtered["name"].astype(str).str.lower().str.contains(q)
        )
        filtered = filtered[mask]

    # Threshold sliders
    filtered = filtered[filtered["rsi"] >= min_rsi]
    filtered = filtered[filtered["volume_ratio"] >= min_vol_ratio]
    filtered = filtered[filtered["pe"] <= max_pe]

    if partial_only:
        filtered = filtered[filtered["session_partial"] == True]

    return filtered


# ==============================================================================
# Dynamic Fragment: Live Countdown & Auto-Refresh Header
# ==============================================================================
@st.fragment(run_every=2)
def live_status_and_countdown_fragment(market: str) -> None:
    """Displays real-time countdown, market status, and refresh controls with auto-refresh."""
    status_data, err = api_client.get_status(market=market)

    col1, col2, col3, col4 = st.columns([2, 2, 2, 2])

    if status_data:
        m_status = status_data.get("market_status", {})
        is_open = m_status.get("is_open", False)
        status_text = "🟢 OPEN" if is_open else "🔴 CLOSED"
        exchange_time = m_status.get("exchange_time", "")

        with col1:
            st.metric("Market Status", status_text, help=f"Exchange Local: {exchange_time}")

        with col2:
            is_scanning = status_data.get("is_scanning", False)
            scan_state_str = "⏳ Scanning in progress..." if is_scanning else " Idle"
            st.metric("Engine State", scan_state_str)

        with col3:
            eff_int = status_data.get("effective_interval_sec", 60)
            last_sec = status_data.get("last_scan_seconds", 0.0)
            st.metric("Effective Interval", f"{eff_int}s", help=f"Last scan duration: {last_sec}s")

        with col4:
            # Refresh action button
            if st.button("🔄 Trigger Scan Now", use_container_width=True, disabled=is_scanning):
                success, msg, retry_after = api_client.post_refresh(market=market)
                if success:
                    st.success("Scan scheduled successfully! Results will update shortly.")
                else:
                    st.warning(msg or "Refresh currently not permitted.")
    else:
        st.error(f"Cannot reach API status service: {err}")


# ==============================================================================
# Main Application Flow
# ==============================================================================
def main() -> None:
    st.title("⚡ Quantitative Stock Screener")
    st.caption(
        "Screening criteria: **RSI(14) > 50**, **Volume > 2x 20-day Average**, **Trailing P/E < 20**. "
        "Notice: Market data is provided by yfinance. Data is delayed and not real-time."
    )

    # 1. Sidebar Controls
    with st.sidebar:
        st.header("Screening Controls")

        market_choice = st.selectbox(
            "Target Market",
            options=["NSE", "NYSE"],
            index=0,
            help="Select market universe for screening (NSE or NYSE).",
        )
        active_market = market_choice

        st.subheader("Filter Adjustments")
        search_query = st.text_input("Search Ticker / Name", placeholder="e.g. INFY, Sun TV")
        slider_rsi = st.slider("Minimum RSI", min_value=30.0, max_value=80.0, value=50.0, step=1.0)
        slider_vol = st.slider("Minimum Volume Ratio", min_value=1.0, max_value=10.0, value=2.0, step=0.1)
        slider_pe = st.slider("Maximum Trailing P/E", min_value=5.0, max_value=50.0, value=20.0, step=1.0)
        partial_only = st.checkbox("Show Partial Sessions Only", value=False)

        st.divider()
        st.subheader("Runtime Configuration")
        curr_interval, _ = api_client.get_settings()
        if curr_interval is not None:
            new_interval = st.number_input(
                "Refresh Interval (seconds)",
                min_value=30,
                max_value=300,
                value=int(curr_interval),
                step=10,
                help="Allowed range: 30 to 300 seconds.",
            )
            if st.button("Apply New Interval"):
                ok, set_msg = api_client.put_settings(new_interval)
                if ok:
                    st.success("Interval updated!")
                else:
                    st.error(set_msg)

    # 2. Render Live Fragment Header
    live_status_and_countdown_fragment(market=active_market)

    # 3. Results live in their own fragment so they re-poll the API on a timer
    results_fragment(
        market=active_market,
        search_query=search_query,
        min_rsi=slider_rsi,
        min_vol_ratio=slider_vol,
        max_pe=slider_pe,
        partial_only=partial_only,
    )


CURRENCY = {"NSE": "₹", "NYSE": "$"}
RESULTS_POLL_SEC = 10


@st.fragment(run_every=RESULTS_POLL_SEC)
def results_fragment(
    market: str,
    search_query: str,
    min_rsi: float,
    min_vol_ratio: float,
    max_pe: float,
    partial_only: bool,
) -> None:
    """Fetches and renders results; re-runs on a timer so new scans appear without interaction."""
    results_data, err = api_client.get_results(market=market)

    if err:
        st.error(err)
        return

    if not results_data:
        st.info("No scan results currently available.")
        return

    meta = results_data.get("meta", {})
    results_list = results_data.get("results", [])
    failures_list = results_data.get("failed_symbols", [])

    # 4. Stale Banner
    if meta.get("stale"):
        reasons = meta.get("stale_reasons", [])
        reasons_str = "; ".join(reasons) if reasons else "Data freshness threshold exceeded."
        st.warning(
            f"⚠️ **STALE DATA NOTICE**: Displayed screening results may be outdated. "
            f"Reason(s): {reasons_str}"
        )

    # 5. Screening Results Table
    st.subheader(f"Screening Survivors ({len(results_list)} Stocks Meeting All Criteria)")

    if results_list:
        df = pd.DataFrame(results_list)
        filtered_df = filter_results_dataframe(
            df=df,
            search_query=search_query,
            min_rsi=min_rsi,
            min_vol_ratio=min_vol_ratio,
            max_pe=max_pe,
            partial_only=partial_only,
        )

        display_cols = [
            "ticker",
            "name",
            "market",
            "price",
            "pe",
            "rsi",
            "volume",
            "avg_volume_20d",
            "volume_ratio",
            "score",
            "session_partial",
            "bar_date",
        ]
        available_cols = [c for c in display_cols if c in filtered_df.columns]
        view_df = filtered_df[available_cols].copy()

        st.dataframe(
            view_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "ticker": st.column_config.TextColumn("Ticker"),
                "name": st.column_config.TextColumn("Company Name"),
                "price": st.column_config.NumberColumn(f"Price ({CURRENCY.get(market, '')})", format="%.2f"),
                "pe": st.column_config.NumberColumn("P/E Ratio", format="%.2f"),
                "rsi": st.column_config.NumberColumn("RSI(14)", format="%.2f"),
                "volume": st.column_config.NumberColumn("Latest Vol", format="%d"),
                "avg_volume_20d": st.column_config.NumberColumn("20d Avg Vol", format="%.0f"),
                "volume_ratio": st.column_config.NumberColumn("Vol Ratio", format="%.2fx"),
                "score": st.column_config.NumberColumn("Rank Score", format="%.4f"),
                "session_partial": st.column_config.CheckboxColumn("Partial"),
                "bar_date": st.column_config.DateColumn("Bar Date"),
            },
        )

        # CSV Export Button
        csv_text = view_df.to_csv(index=False)
        st.download_button(
            label="📥 Export Filtered Table to CSV",
            data=csv_text,
            file_name=f"screener_{market}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )
    else:
        st.info("No stocks currently satisfy all three screening conditions.")

    # 6. Diagnostic Funnel & Metrics Expander
    funnel = meta.get("funnel", {})
    with st.expander("📊 Screening Funnel & Pipeline Diagnostics", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Constituents in Universe", funnel.get("universe", 0))
        c2.metric("Successfully Fetched", funnel.get("fetched", 0))
        c3.metric("Passed RSI & Volume", funnel.get("passed_rsi_volume", 0))
        c4.metric("Qualified (Passed P/E)", funnel.get("passed_pe", 0))

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Filtered by RSI", funnel.get("filtered_rsi", 0))
        c6.metric("Filtered by Volume", funnel.get("filtered_volume", 0))
        c7.metric("Filtered by P/E", funnel.get("filtered_pe", 0))
        c8.metric("Data / System Failures", funnel.get("failed", 0))
        if funnel.get("filtered_liquidity", 0):
            st.caption(f"Also excluded by liquidity floor: {funnel['filtered_liquidity']}")

    # 7. Failed Symbols Expander
    if failures_list:
        with st.expander(f"⚠️ Failed Symbols & Data Issues ({len(failures_list)})", expanded=False):
            fail_df = pd.DataFrame(failures_list)
            st.dataframe(fail_df, use_container_width=True, hide_index=True)

    # 8. Footer
    st.markdown("---")
    st.caption("Disclaimer: For algorithmic and technical research purposes only. Not financial advice.")


if __name__ == "__main__":
    main()

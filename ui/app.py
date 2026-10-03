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
import plotly.express as px

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
            if st.button("🔄 Trigger Scan Now", use_container_width=True, disabled=is_scanning):
                success, msg, retry_after = api_client.post_refresh(market=market)
                if success:
                    st.toast("Scan scheduled successfully! Results will update shortly.", icon="🔄")
                else:
                    st.toast(msg or "Refresh currently not permitted.", icon="⚠️")
    else:
        st.error(f"Cannot reach API status service: {err}")


def apply_custom_css():
    st.markdown("""
        <style>
        /* Import Inter font */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        
        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif !important;
        }
        
        /* Metric cards styling */
        [data-testid="stMetric"] {
            background-color: #FFFFFF;
            border: 1px solid #E5E7EB;
            border-radius: 8px;
            padding: 16px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            transition: all 0.2s ease-in-out;
        }
        
        [data-testid="stMetric"]:hover {
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            transform: translateY(-2px);
        }
        
        /* Metric value styling */
        [data-testid="stMetricValue"] {
            color: #212529;
            font-weight: 700;
        }
        </style>
    """, unsafe_allow_html=True)


# ==============================================================================
# Main Application Flow
# ==============================================================================
def main() -> None:
    apply_custom_css()
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
        slider_rsi = st.slider("Minimum RSI", min_value=50.0, max_value=80.0, value=50.0, step=1.0)
        slider_vol = st.slider("Minimum Volume Ratio", min_value=2.0, max_value=10.0, value=2.0, step=0.1)
        slider_pe = st.slider("Maximum Trailing P/E", min_value=5.0, max_value=20.0, value=20.0, step=1.0)
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

    # 5. Tabbed Interface
    tab_results, tab_visualizations, tab_diagnostics = st.tabs([
        "📊 Screener Results",
        "📈 Visualizations",
        "⚙️ Diagnostics"
    ])

    with tab_results:
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

            if len(view_df) > 0:
                st.markdown("### 🏆 Top Ranked Stock")
                top_stock = view_df.iloc[0]
                sc1, sc2, sc3, sc4 = st.columns(4)
                
                # Format price with currency
                price_str = f"{CURRENCY.get(market, '')}{top_stock['price']:.2f}"
                sc1.metric(f"Top Pick: {top_stock['ticker']}", price_str, f"Score: {top_stock['score']:.4f}")
                
                # Show RSI
                sc2.metric("RSI (14)", f"{top_stock['rsi']:.1f}")
                
                # Show Volume Ratio with volume as delta
                vol_ratio = float(top_stock['volume_ratio'])
                vol_str = f"{top_stock['volume']:,.0f} vol"
                sc3.metric("Volume Ratio", f"{vol_ratio:.1f}x", vol_str, delta_color="normal")
                
                # P/E Ratio
                pe_val = float(top_stock['pe'])
                sc4.metric("Trailing P/E", f"{pe_val:.1f}")

                st.markdown("---")
                st.markdown("### 📋 All Qualified Stocks")

            st.dataframe(
                view_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "ticker": st.column_config.TextColumn("Ticker"),
                    "name": st.column_config.TextColumn("Company Name"),
                    "price": st.column_config.NumberColumn(f"Price ({CURRENCY.get(market, '')})", format="%.2f"),
                    "pe": st.column_config.NumberColumn("P/E Ratio", format="%.2f"),
                    "rsi": st.column_config.ProgressColumn("RSI(14)", format="%.2f", min_value=0, max_value=100),
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

    with tab_visualizations:
        st.subheader("Data Insights")
        if results_list and not view_df.empty:
            col1, col2 = st.columns(2)
            
            with col1:
                fig_scatter = px.scatter(
                    view_df,
                    x="pe",
                    y="volume_ratio",
                    size="rsi",
                    color="score",
                    hover_name="ticker",
                    hover_data=["name", "price", "rsi"],
                    title="Value vs. Momentum",
                    labels={"pe": "Trailing P/E", "volume_ratio": "Volume Ratio (x)", "score": "Rank Score"},
                    color_continuous_scale="Blues",
                )
                fig_scatter.update_layout(margin=dict(l=20, r=20, t=40, b=20), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_scatter, use_container_width=True)
                
            with col2:
                fig_hist = px.histogram(
                    view_df,
                    x="rsi",
                    nbins=10,
                    title="RSI Distribution",
                    labels={"rsi": "RSI(14)"},
                    color_discrete_sequence=["#0D6EFD"],
                )
                fig_hist.update_layout(margin=dict(l=20, r=20, t=40, b=20), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_hist, use_container_width=True)
        else:
            st.info("No data available to visualize. Adjust filters or wait for more results.")

    with tab_diagnostics:
        # 6. Diagnostic Funnel & Metrics
        funnel = meta.get("funnel", {})
        st.subheader("📊 Screening Funnel & Pipeline Diagnostics")
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

        # 7. Failed Symbols
        if failures_list:
            st.markdown("---")
            st.subheader(f"⚠️ Failed Symbols & Data Issues ({len(failures_list)})")
            fail_df = pd.DataFrame(failures_list)
            st.dataframe(fail_df, use_container_width=True, hide_index=True)

    # 8. Footer
    st.markdown("---")
    st.caption("Disclaimer: For algorithmic and technical research purposes only. Not financial advice.")


if __name__ == "__main__":
    main()

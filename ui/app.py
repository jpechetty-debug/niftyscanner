"""Light Streamlit workspace. All screening data comes through the HTTP API."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import os
from pathlib import Path
import sys
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api_client import ScreenerApiClient

st.set_page_config(page_title="Stock Screener | Nifty 500 & NYSE", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")
api_client = ScreenerApiClient()
CURRENCY = {"NSE": "₹", "NYSE": "$"}
TIMEZONES = {"NSE": "Asia/Kolkata", "NYSE": "America/New_York"}
RESULTS_POLL_SEC = 10


def filter_results_dataframe(
    df: pd.DataFrame, search_query: str, min_rsi: Optional[float],
    min_vol_ratio: Optional[float], max_pe: Optional[float], partial_only: bool,
) -> pd.DataFrame:
    """Narrow API results only when the user enables a filter."""
    if df.empty:
        return df
    filtered = df.copy()
    if search_query.strip():
        query = search_query.strip().lower()
        filtered = filtered[
            filtered["ticker"].astype(str).str.lower().str.contains(query, regex=False)
            | filtered["name"].astype(str).str.lower().str.contains(query, regex=False)
        ]
    if min_rsi is not None:
        filtered = filtered[filtered["rsi"] >= min_rsi]
    if min_vol_ratio is not None:
        filtered = filtered[filtered["volume_ratio"] >= min_vol_ratio]
    if max_pe is not None:
        filtered = filtered[filtered["pe"] <= max_pe]
    if partial_only:
        filtered = filtered[filtered["session_partial"]]
    return filtered


def format_scan_time(value: Optional[str], market: str) -> str:
    """Display API timestamps in exchange time without guessing missing values."""
    if not value:
        return "Awaiting first scan"
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(ZoneInfo(TIMEZONES[market]))
        return stamp.strftime("%d %b %Y · %H:%M %Z").strip()
    except (ValueError, TypeError):
        return "Time unavailable"


def apply_custom_css() -> None:
    """Load the local stylesheet; no CDN, script, or remote font is required."""
    css = Path(__file__).with_name("styles.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def status_card(label: str, value: str, detail: str, tone: str = "neutral") -> None:
    """Render escaped API status in a compact presentation card."""
    st.markdown(
        f'<div class="status-card {tone}"><div class="eyebrow">{escape(label)}</div>'
        f'<div class="status-value">{escape(value)}</div>'
        f'<div class="card-detail">{escape(detail)}</div></div>', unsafe_allow_html=True)


def result_card(row: pd.Series, market: str, spotlight: bool = False) -> None:
    """Present one API result, escaping company and ticker text before HTML."""
    items = [("Close price", f"{CURRENCY.get(market, '')}{row['price']:,.2f}"),
             ("Score", f"{row['score']:.4f}"), ("RSI", f"{row['rsi']:.1f}"),
             ("Volume surge", f"{row['volume_ratio']:.2f}×"), ("Trailing P/E", f"{row['pe']:.2f}")]
    stats = ''.join(f'<div class="result-stat"><span>{label}</span><strong>{escape(value)}</strong></div>'
                    for label, value in items)
    badge = '<span class="rank-badge">★ HIGHEST RANKED IN THIS VIEW</span>' if spotlight else ''
    session = escape(str(row.get('bar_date', 'Unavailable')))
    partial = ' · Partial session' if row.get('session_partial') else ''
    st.markdown(
        f'<article class="result-card {"spotlight" if spotlight else ""}">{badge}'
        f'<div class="result-identity"><strong>{escape(str(row["ticker"]))}</strong>'
        f'<span>{escape(str(row["name"]))}</span></div><div class="result-stats">{stats}</div>'
        f'<div class="card-detail">Data session: {session}{partial}</div></article>', unsafe_allow_html=True)


def render_summary(results: list[dict], funnel: dict, market: str) -> None:
    """Show scan-wide counts and rates separately from optional view filters."""
    universe = funnel.get("universe", 0)
    fetched = funnel.get("fetched", 0)
    failed = funnel.get("failed", 0)
    pass_rate = f"{len(results) / universe:.1%} pass rate" if universe else "Awaiting scan"
    cards = [("Qualified candidates", len(results), "meeting all rules", pass_rate,
              len(results) / universe if universe else 0, ""),
             ("Universe size", universe, "symbols in universe", "Nifty 500" if market == "NSE" else "NYSE",
              fetched / universe if universe else 0, ""),
             ("Data issues", failed, "affected symbols", "Review scan details" if failed else "No issues reported",
              failed / universe if universe else 0, "amber" if failed else "")]
    html = '<div class="summary-grid">'
    for label, count, detail, badge, ratio, tone in cards:
        html += (f'<div class="summary-card"><div class="summary-top"><span class="eyebrow">{label}</span>'
                 f'<span class="pill {tone}">{badge}</span></div><div class="summary-number">{count:,}'
                 f'<span>{detail}</span></div><div class="meter"><span style="width:{min(1, max(0, ratio)) * 100:.2f}%">'
                 '</span></div></div>')
    st.markdown(html + '</div>', unsafe_allow_html=True)


def move_result_page(market: str, delta: int) -> None:
    """Advance a paginated view using Streamlit's widget callback order."""
    st.session_state[f"page_{market}"] += delta


def reset_view_filters() -> None:
    """Reset only the optional view filters, leaving backend settings intact."""
    for key, value in {"search": "", "partial_only": False, "refine": False,
                       "min_rsi": 0.0, "min_volume": 0.0, "limit_pe": False, "max_pe": 0.0}.items():
        st.session_state[key] = value


@st.fragment(run_every=2)
def live_status_and_countdown_fragment(market: str) -> None:
    """Poll exchange status and the backend's actual next-scan deadline."""
    status_data, error = api_client.get_status(market=market)
    if not status_data:
        st.error(f"Unable to connect to the scanner. {error or 'Please check the API service.'}")
        return
    market_status = status_data.get("market_status", {})
    scanning = status_data.get("is_scanning", False)
    deadline = status_data.get("next_refresh_at")
    remaining = None
    if deadline:
        remaining = max(0, int((datetime.fromisoformat(deadline) - datetime.now(timezone.utc)).total_seconds()))
    market_col, scan_col, next_col, action_col = st.columns([1, 1, 1, .9])
    with market_col:
        status_card("Market session", f"{market} Market {'Open' if market_status.get('is_open') else 'Closed'}",
                    "Exchange-local session", "positive" if market_status.get("is_open") else "neutral")
    breaker_open = any(str(status_data.get(key, "")).upper() == "OPEN"
                       for key in ("download_breaker", "pe_breaker"))
    with scan_col:
        status_card("Scan engine", "Scanning" if scanning else ("Cooling down" if breaker_open else "Ready"),
                    f"Last scan: {status_data.get('last_scan_seconds', 0):.1f}s",
                    "amber" if breaker_open else "positive")
    next_label = f"{remaining // 60:02d}:{remaining % 60:02d}" if remaining is not None else (
        "Scanning" if scanning else "Paused")
    with next_col:
        status_card("Scan pipeline", next_label,
                    "Minutes : seconds" if remaining is not None else "No automatic scan scheduled",
                    "neutral" if remaining is not None else "amber")
    with action_col:
        st.markdown('<div class="eyebrow execution-label">ON-DEMAND SCAN</div>', unsafe_allow_html=True)
        if st.button("Scan now", type="primary", width="stretch", disabled=scanning):
            accepted, message, retry_after = api_client.post_refresh(market=market)
            if accepted:
                st.toast("Scan queued. Results will update automatically.", icon="✅")
                st.rerun()
            else:
                detail = message or "Scan unavailable. Please try again."
                if retry_after and f"{retry_after}s" not in detail:
                    detail += f" Retry in {retry_after}s."
                st.toast(detail, icon="⚠️")
    if remaining is not None:
        st.caption(f"Next refresh in {remaining}s · Exchange time: {market_status.get('exchange_time', 'unavailable')}")
    else:
        st.caption(f"{market_status.get('exchange_time', 'Exchange time unavailable')} · "
                   + ("Scan in progress" if scanning else "No automatic scan scheduled"))
    if market_status.get("is_holiday"):
        st.caption("No trading session today. The latest available results remain accessible.")


def main() -> None:
    apply_custom_css()
    with st.sidebar:
        st.markdown('<div class="sidebar-brand"><span class="brand-icon">▥</span>'
                    '<strong>Stock Screener</strong></div>', unsafe_allow_html=True)
        st.caption("Momentum · Activity · Valuation")
        st.divider()
        market = st.selectbox("Market universe", ["NSE", "NYSE"],
                              format_func=lambda name: "NSE · Nifty 500" if name == "NSE" else "NYSE · US equities")
        st.divider()
        st.subheader("Refine results")
        st.caption("These controls narrow the qualified list.")
        search = st.text_input("Search stocks", placeholder="Ticker or company name", key="search")
        partial_only = st.checkbox("Current session only", key="partial_only", help="Only stocks with a partial daily bar while the market is open. This view will be empty when the market is closed.")
        refine = st.toggle("Use indicator filters", value=False, key="refine")
        with st.expander("↗  Momentum & trend", expanded=True):
            rsi = st.slider("Minimum RSI", 0.0, 100.0, 0.0, 1.0, disabled=not refine, key="min_rsi")
            st.caption("0 Oversold · 50 Neutral · 100 Overbought")
        with st.expander("▥  Volume & liquidity", expanded=True):
            volume = st.number_input("Minimum volume ratio", min_value=0.0, value=0.0, step=0.1, disabled=not refine, key="min_volume")
            st.caption("Multiple of completed-session average")
        with st.expander("₹  Valuation", expanded=True):
            limit_pe = st.checkbox("Limit trailing P/E", disabled=not refine, key="limit_pe")
            pe = st.number_input("Maximum trailing P/E", value=0.0, step=1.0, disabled=not (refine and limit_pe), key="max_pe",
                                 help="Negative and zero P/E values can be included in the qualified list.")
        st.divider()
        st.button("Reset filters", width="stretch", on_click=reset_view_filters)
        with st.expander("Refresh settings"):
            interval, error = api_client.get_settings()
            if interval is not None:
                new_interval = st.number_input("Refresh interval (seconds)", min_value=30,
                                               max_value=300, value=int(interval), step=10)
                if st.button("Apply interval", width="stretch"):
                    ok, message = api_client.put_settings(int(new_interval))
                    st.success("Refresh interval updated.") if ok else st.error(message)
            else:
                st.caption(error or "Settings unavailable.")
        st.caption("Data from Yahoo Finance may be delayed and is not real-time.")
        st.caption("NSE session calendar uses the XBOM proxy.")
    st.markdown(f'<div class="terminal-bar"><div><span class="brand-icon">▥</span>'
                f'<strong>Stock Screener</strong><span class="terminal-tag">Terminal</span></div>'
                f'<span class="terminal-market">{market} · Research workspace</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="workspace-label">MOMENTUM / VOLUME / VALUATION</div>', unsafe_allow_html=True)
    st.title("High-Momentum Stock Screener")
    st.caption("Explore ranked stocks that pass every screening rule. Yahoo Finance data is delayed and not real-time.")
    live_status_and_countdown_fragment(market)
    results_fragment(market, search, rsi if refine else None, volume if refine else None,
                     pe if refine and limit_pe else None, partial_only)


@st.fragment(run_every=RESULTS_POLL_SEC)
def results_fragment(
    market: str, search_query: str, min_rsi: Optional[float],
    min_vol_ratio: Optional[float], max_pe: Optional[float], partial_only: bool,
) -> None:
    """Render qualified results, clear empty states, light charts and scan details."""
    response, error = api_client.get_results(market=market)
    if error or not response:
        st.error(error or "No results available. Check the scanner connection.")
        st.caption("Not financial advice.")
        return
    meta = response.get("meta", {})
    results = response.get("results", [])
    failures = response.get("failed_symbols", [])
    funnel = meta.get("funnel", {})
    if meta.get("stale"):
        st.warning("Saved results may be outdated. " + "; ".join(meta.get("stale_reasons", []) or ["Awaiting a successful scan."]))
    st.caption(f"Last refreshed: {format_scan_time(meta.get('last_refreshed'), market)}  ·  "
               f"Data session: {format_scan_time(meta.get('data_as_of'), market)}")
    render_summary(results, funnel, market)
    df = pd.DataFrame(results)
    filtered = filter_results_dataframe(df, search_query, min_rsi, min_vol_ratio, max_pe, partial_only)
    if not filtered.empty:
        best = filtered.sort_values(["score", "volume_ratio", "ticker"], ascending=[False, False, True]).iloc[0]
        result_card(best, market, spotlight=True)
    results_tab, charts_tab, details_tab = st.tabs(["Qualified stocks", "Insights", "Scan details"])
    with results_tab:
        if not results:
            st.info("No qualified stocks in this scan. Check Scan details for filtering and data issues.")
        else:
            tools = st.columns([2, 2, 1.3])
            tools[0].caption(f"Showing {len(filtered):,} of {len(results):,} qualified stocks")
            sort = tools[1].selectbox("Sort results", ["Rank score", "Volume ratio", "RSI", "P/E", "Company"],
                                      label_visibility="collapsed", key=f"sort_{market}")
            layout = tools[2].radio("Result layout", ["Table", "Cards"], horizontal=True,
                                    label_visibility="collapsed", key=f"layout_{market}")
            sort_fields = {"Rank score": (["score", "volume_ratio", "ticker"], [False, False, True]),
                           "Volume ratio": (["volume_ratio", "ticker"], [False, True]),
                           "RSI": (["rsi", "ticker"], [False, True]),
                           "P/E": (["pe", "ticker"], [True, True]),
                           "Company": (["name", "ticker"], [True, True])}
            fields, ascending = sort_fields[sort]
            filtered = filtered.sort_values(fields, ascending=ascending, kind="stable")
            page_key = f"page_{market}"
            signature = (search_query, min_rsi, min_vol_ratio, max_pe, partial_only, sort)
            if st.session_state.get(f"view_signature_{market}") != signature:
                st.session_state[page_key] = 1
                st.session_state[f"view_signature_{market}"] = signature
            if filtered.empty:
                st.info("No stocks match your view. Clear the search or loosen the sidebar filters.")
            else:
                volume_details = st.checkbox("Show volume details", value=False, key=f"volume_details_{market}")
                page_count = (len(filtered) + 5) // 6
                st.session_state[page_key] = min(page_count, st.session_state.get(page_key, 1))
                page = st.session_state[page_key]
                offset = (page - 1) * 6
                page_rows = filtered.iloc[offset:offset + 6]
                columns = ["ticker", "name", "score", "price", "rsi", "volume_ratio", "pe", "session_partial", "bar_date"]
                if volume_details:
                    columns += ["volume", "avg_volume_20d", "market"]
                view = page_rows[[name for name in columns if name in filtered]].copy()
                if "bar_date" in view:
                    view["bar_date"] = pd.to_datetime(view["bar_date"]).dt.date
                if layout == "Cards":
                    for _, row in page_rows.iterrows():
                        result_card(row, market)
                        if volume_details:
                            st.caption(f"Observed volume: {row['volume']:,.0f} · Average volume: {row['avg_volume_20d']:,.0f}")
                else:
                    st.dataframe(view, width="stretch", hide_index=True, row_height=48,
                             column_config={
                                 "ticker": st.column_config.TextColumn("Ticker", width="small"),
                                 "name": st.column_config.TextColumn("Company", width="medium"),
                                 "score": st.column_config.NumberColumn("Score", format="%.4f"),
                                 "price": st.column_config.NumberColumn(f"Price ({CURRENCY.get(market, '')})", format="%.2f"),
                                 "rsi": st.column_config.ProgressColumn("RSI", min_value=0, max_value=100, format="%.1f"),
                                 "volume_ratio": st.column_config.NumberColumn("Volume ×", format="%.2f", help="Compared with completed-session average. Partial-session ratios use a bounded projection."),
                                 "pe": st.column_config.NumberColumn("Trailing P/E", format="%.2f"),
                                 "session_partial": st.column_config.CheckboxColumn("Partial", help="Today's daily bar while the exchange is open. Volume is incomplete."),
                                 "bar_date": st.column_config.DateColumn("Session", format="DD MMM YYYY"),
                                 "volume": st.column_config.NumberColumn("Observed volume", format="%d"),
                                 "avg_volume_20d": st.column_config.NumberColumn("Average volume", format="%.0f"),
                                 })
                pagination = st.columns([3, 1, 1, 1])
                pagination[0].caption(f"Showing {offset + 1}–{offset + len(page_rows)} of {len(filtered):,} matching stocks")
                pagination[1].button("Previous", key=f"previous_{market}", disabled=page == 1,
                                     on_click=move_result_page, args=(market, -1), width="stretch")
                pagination[2].selectbox("Page", list(range(1, page_count + 1)), key=page_key,
                                        label_visibility="collapsed", format_func=lambda p: f"Page {p}")
                pagination[3].button("Next", key=f"next_{market}", disabled=page == page_count,
                                     on_click=move_result_page, args=(market, 1), width="stretch")
                st.download_button("Download filtered CSV", filtered.to_csv(index=False),
                                   file_name=f"screener_{market}_{datetime.now():%Y%m%d_%H%M%S}.csv",
                                   mime="text/csv", on_click="ignore")
                if filtered["session_partial"].any():
                    st.caption("Partial bars contain incomplete volume. Projected ratios may overestimate or underestimate closing volume.")
    with charts_tab:
        st.subheader("Explore this view")
        if filtered.empty:
            st.info("Charts appear when stocks match your current filters.")
        else:
            left, right = st.columns(2)
            scatter = px.scatter(filtered, x="pe", y="volume_ratio", size="rsi", color="score",
                                 hover_name="ticker", hover_data=["name", "price", "rsi"],
                                 title="Valuation & trading activity", template="plotly_white",
                                 labels={"pe": "Trailing P/E", "volume_ratio": "Volume ratio", "score": "Score"},
                                 color_continuous_scale="Purples")
            histogram = px.histogram(filtered, x="rsi", nbins=10, title="Momentum distribution",
                                     template="plotly_white", labels={"rsi": "RSI", "count": "Stocks"},
                                     color_discrete_sequence=["#4F46E5"])
            histogram.update_layout(yaxis_title="Stocks")
            for column, figure in ((left, scatter), (right, histogram)):
                figure.update_layout(paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
                                     font_color="#212529", margin=dict(l=16, r=16, t=48, b=16),
                                     xaxis=dict(gridcolor="#EDF0F3"), yaxis=dict(gridcolor="#EDF0F3"))
                with column:
                    st.plotly_chart(figure, width="stretch", theme=None)
    with details_tab:
        st.subheader("Scan details")
        st.caption(f"Scan duration: {meta.get('scan_seconds', 0):.1f}s · "
                   f"Provider attempts: {meta.get('request_count', 0):,} · "
                   f"Refresh interval: {meta.get('effective_interval_sec', 0)}s")
        stages = [("Universe", "universe"), ("Data fetched", "fetched"),
                  ("Passed RSI & volume", "passed_rsi_volume"), ("Qualified", "passed_pe"),
                  ("Filtered by RSI", "filtered_rsi"), ("Filtered by volume", "filtered_volume"),
                  ("Filtered by P/E", "filtered_pe"), ("Data issues", "failed")]
        for offset in (0, 4):
            for column, (label, key) in zip(st.columns(4), stages[offset:offset + 4]):
                column.metric(label, f"{funnel.get(key, 0):,}")
        if funnel.get("filtered_liquidity", 0):
            st.caption(f"Filtered by liquidity: {funnel['filtered_liquidity']:,}")
        if failures:
            with st.expander(f"Data issue records ({len(failures):,})"):
                st.dataframe(pd.DataFrame(failures), width="stretch", hide_index=True)
        else:
            st.caption("No data issue records reported.")
    st.divider()
    st.caption("For research purposes. Not financial advice.")


if __name__ == "__main__":
    main()

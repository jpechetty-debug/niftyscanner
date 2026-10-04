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


def render_terminal_header(market: str, connected: bool) -> None:
    """Show connection state only after checking the HTTP status endpoint."""
    logo = ('<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8">'
            '<rect x="3" y="13" width="4" height="8" rx="1"/>'
            '<rect x="10" y="8" width="4" height="13" rx="1"/>'
            '<rect x="17" y="3" width="4" height="18" rx="1"/></svg>')
    st.markdown(
        '<header class="terminal-bar"><div class="terminal-brand">'
        f'<span class="brand-icon">{logo}</span><strong>Stock Screener Terminal</strong>'
        f'<span class="terminal-tag">{escape(market)}</span></div>'
        f'<div title="Scanner HTTP API connection" class="connection-state {"connected" if connected else "disconnected"}">'
        f'<span class="status-dot"></span>{"CONNECTED" if connected else "DISCONNECTED"}'
        '<span class="terminal-context">Local research workspace</span></div></header>', unsafe_allow_html=True)


def request_scan(market: str) -> None:
    """Queue either scan control through HTTP, including real cooldown feedback."""
    accepted, message, retry_after = api_client.post_refresh(market=market)
    if accepted:
        st.toast("Scan queued. Results will update automatically.", icon="✅")
        st.rerun()
    else:
        detail = message or "Scan unavailable. Please try again."
        if retry_after and f"{retry_after}s" not in detail:
            detail += f" Retry in {retry_after}s."
        st.toast(detail, icon="⚠️")


def status_card(label: str, value: str, detail: str, tone: str = "neutral", badge: str = "") -> None:
    """Render escaped API status in a compact presentation card."""
    st.markdown(
        f'<div class="status-card {tone}"><div class="status-top"><span>{escape(label)}</span>'
        '<span class="status-dot"></span></div>'
        f'<div class="status-line"><strong class="status-value">{escape(value)}</strong>'
        f'<span class="status-badge">{escape(badge)}</span></div>'
        f'<div class="card-detail">{escape(detail)}</div></div>', unsafe_allow_html=True)


def result_card_html(row: pd.Series, market: str, spotlight: bool = False) -> str:
    """Build reusable result markup with escaped company and ticker text."""
    items = [("Price", f"{CURRENCY.get(market, '')}{row['price']:,.2f}"),
             ("Score", f"{row['score']:.4f}"), ("RSI", f"{row['rsi']:.1f}"),
             ("Volume ×", f"{row['volume_ratio']:.2f}×"), ("P/E", f"{row['pe']:.2f}")]
    stats = ''.join(f'<div class="result-stat"><span>{label}</span><strong>{escape(value)}</strong></div>'
                    for label, value in items)
    badge = ('<div class="rank-line"><span class="rank-badge">HIGHEST RANKED</span>'
             '<span>Rank #1 in this view</span></div>') if spotlight else ''
    session = escape(str(row.get('bar_date', 'Unavailable')))
    partial = ' · Partial session · Volume indicative' if row.get('session_partial') else ''
    return (
        f'<article class="result-card {"spotlight" if spotlight else ""}"><div class="result-main">{badge}'
        f'<div class="result-identity"><strong>{escape(str(row["ticker"]))}</strong>'
        f'<span>{escape(str(row["name"]))}</span></div>'
        f'<div class="card-detail">Data session: {session}{partial}</div></div>'
        f'<div class="result-stats">{stats}</div></article>')


def result_card(row: pd.Series, market: str, spotlight: bool = False) -> None:
    """Present one API result with the same markup as the overview spotlight."""
    st.markdown(result_card_html(row, market, spotlight), unsafe_allow_html=True)


def render_summary(results: list[dict], funnel: dict, market: str,
                   best: Optional[pd.Series] = None) -> None:
    """Combine scan-wide counts with the highest-ranked stock in the current view."""
    universe = funnel.get("universe", 0)
    failed = funnel.get("failed", 0)
    pass_rate = f"{len(results) / universe:.1%}" if universe else "Pending"
    cards = [("Qualified", len(results), "stocks", pass_rate, ""),
             ("Universe", universe, "total", market, "plain"),
             ("Issues", failed, "affected", "Review" if failed else "Clear",
              "amber" if failed else "")]
    html = '<div class="overview-row"><div class="summary-grid">'
    for label, count, detail, badge, tone in cards:
        html += (f'<div class="summary-card"><div class="summary-top"><span class="eyebrow">{label}</span>'
                 f'<span class="pill {tone}">{badge}</span></div><div class="summary-number">{count:,}'
                 f'<span>{detail}</span></div></div>')
    html += '</div>'
    if best is not None:
        html += result_card_html(best, market, spotlight=True)
    st.markdown(html + '</div>', unsafe_allow_html=True)


def render_session_notice(meta: dict, market: str) -> None:
    """Combine the exchange notice and real snapshot timestamps in one strip."""
    market_status = st.session_state.get(f"market_status_{market}", {})
    if market_status.get("is_holiday"):
        message = "Market closed today. Showing the latest available session."
    elif market_status.get("is_open"):
        message = "Market open. Partial-session volume is projected and indicative; Yahoo quotes are delayed."
    else:
        message = "Market closed. Showing the latest available session."
    st.markdown(
        '<div class="session-notice"><div class="notice-message"><span class="notice-icon">◷</span>'
        f'<span>{escape(message)}</span></div><div class="notice-timestamp">Last refreshed: '
        f'<strong>{escape(format_scan_time(meta.get("last_refreshed"), market))}</strong>'
        f'<br>Data session: {escape(format_scan_time(meta.get("data_as_of"), market))}</div></div>',
        unsafe_allow_html=True)


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
    render_terminal_header(market, bool(status_data) and not error)
    with st.container(key="scan_heading"):
        title_col, auto_col, refresh_col = st.columns([5, 1.2, 1])
    with title_col:
        st.markdown('<div class="page-heading"><h1>Stock Screener</h1>'
                    '<p>Momentum, volume and valuation</p></div>', unsafe_allow_html=True)
    scanning = bool(status_data and status_data.get("is_scanning", False))
    with auto_col:
        scheduled = bool(status_data and status_data.get("next_refresh_at"))
        st.markdown(f'<div class="headline-status">Auto-refresh: <strong>{"Scheduled" if scheduled else "Paused"}</strong></div>',
                    unsafe_allow_html=True)
    with refresh_col:
        if st.button("Scan now", type="primary", icon=":material/sync:", width="stretch", disabled=scanning or not status_data):
            request_scan(market)
    if not status_data:
        st.error(f"Unable to connect to the scanner. {error or 'Please check the API service.'}")
        return
    market_status = status_data.get("market_status", {})
    st.session_state[f"market_status_{market}"] = market_status
    deadline = status_data.get("next_refresh_at")
    remaining = None
    if deadline:
        remaining = max(0, int((datetime.fromisoformat(deadline) - datetime.now(timezone.utc)).total_seconds()))
    with st.container(key="scan_status"):
        market_col, scan_col, next_col, action_col = st.columns(4)
    with market_col:
        status_card("Market status", "Open" if market_status.get('is_open') else "Closed",
                    market_status.get("exchange_time", f"{market} · Exchange time unavailable"),
                    "positive" if market_status.get("is_open") else "neutral",
                    "Holiday / Weekend" if market_status.get("is_holiday") else "")
    breaker_open = any(str(status_data.get(key, "")).upper() == "OPEN"
                       for key in ("download_breaker", "pe_breaker"))
    with scan_col:
        status_card("Scanner status", "Scanning" if scanning else ("Cooling down" if breaker_open else "Ready"),
                    f"Last scan: {status_data.get('last_scan_seconds', 0):.1f}s",
                    "amber" if breaker_open else "positive", "Circuit open" if breaker_open else "")
    next_label = f"{remaining // 60:02d}:{remaining % 60:02d}" if remaining is not None else (
        "Scanning" if scanning else "Not scheduled")
    with next_col:
        status_card("Next scan", next_label,
                    "Minutes : seconds" if remaining is not None else ("Scan in progress" if scanning else "Run a scan when needed"),
                    "neutral" if remaining is not None else "amber")
        if remaining is not None:
            st.caption(f"Next refresh in {remaining}s")
    with action_col:
        with st.container(border=True, key="execution_mode"):
            st.markdown('<div class="execution-heading"><span>Manual scan</span><span>On demand</span></div>', unsafe_allow_html=True)
            if st.button("Run scan", width="stretch", disabled=scanning):
                request_scan(market)


def main() -> None:
    apply_custom_css()
    with st.sidebar:
        st.markdown('<div class="sidebar-brand"><span class="status-dot"></span><strong>Filters &amp; screener rules</strong></div>', unsafe_allow_html=True)
        market = st.selectbox("Market universe", ["NSE", "NYSE"],
                              format_func=lambda name: "NSE · Nifty 500" if name == "NSE" else "NYSE · US equities")
        search = st.text_input("Search stocks", placeholder="Ticker or company name", key="search")
        refine = st.toggle("Use indicator filters", value=False, key="refine")
        with st.expander(f"Momentum · {int(refine)} active", expanded=True):
            rsi = st.slider("Minimum RSI", 0.0, 100.0, 0.0, 1.0, disabled=not refine, key="min_rsi")
        with st.expander(f"Volume · {int(refine)} active", expanded=True):
            volume = st.number_input("Minimum volume ratio", min_value=0.0, value=0.0, step=0.1, disabled=not refine, key="min_volume")
            partial_only = st.checkbox("Current session only", key="partial_only", help="Only stocks with a partial daily bar while the market is open. This view will be empty when the market is closed.")
        with st.expander(f"Valuation · {int(refine and st.session_state.get('limit_pe', False))} active", expanded=True):
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
        st.markdown('<div class="sidebar-notice">ⓘ &nbsp; Yahoo Finance data is delayed and not real-time.'
                    '<br>NSE session calendar uses the XBOM proxy.</div>', unsafe_allow_html=True)
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
    render_session_notice(meta, market)
    if meta.get("stale"):
        st.warning("Saved results may be outdated. " + "; ".join(meta.get("stale_reasons", []) or ["Awaiting a successful scan."]))
    df = pd.DataFrame(results)
    filtered = filter_results_dataframe(df, search_query, min_rsi, min_vol_ratio, max_pe, partial_only)
    best = None
    if not filtered.empty:
        best = filtered.sort_values(["score", "volume_ratio", "ticker"], ascending=[False, False, True]).iloc[0]
    render_summary(results, funnel, market, best)
    results_tab, charts_tab, details_tab, performance_tab = st.tabs([f"Qualified stocks · {len(results)}", "Market insights", "Scan details", "Performance"])
    with results_tab:
        if not results:
            st.info("No qualified stocks in this scan. Check Scan details for filtering and data issues.")
        else:
            with st.container(key="results_controls"):
                tools = st.columns([1.2, 1.6, 1.3, 1.1])
            volume_details = tools[0].checkbox("Volume details", value=False, key=f"volume_details_{market}")
            sort = tools[1].selectbox("Sort results", ["Rank score", "Volume ratio", "RSI", "P/E", "Company"],
                                      label_visibility="collapsed", key=f"sort_{market}",
                                      format_func=lambda value: {"Rank score": "Score · high to low", "Volume ratio": "Volume ratio · high to low",
                                                                 "RSI": "RSI · high to low", "P/E": "P/E · low to high", "Company": "Company · A–Z"}[value])
            layout = tools[2].radio("Result layout", ["Table", "Cards"], horizontal=True,
                                    label_visibility="collapsed", key=f"layout_{market}")
            sort_fields = {"Rank score": (["score", "volume_ratio", "ticker"], [False, False, True]),
                           "Volume ratio": (["volume_ratio", "ticker"], [False, True]),
                           "RSI": (["rsi", "ticker"], [False, True]),
                           "P/E": (["pe", "ticker"], [True, True]),
                           "Company": (["name", "ticker"], [True, True])}
            fields, ascending = sort_fields[sort]
            filtered = filtered.sort_values(fields, ascending=ascending, kind="stable")
            tools[3].download_button("Export CSV", filtered.to_csv(index=False),
                                     file_name=f"screener_{market}_{datetime.now():%Y%m%d_%H%M%S}.csv",
                                     mime="text/csv", on_click="ignore", icon=":material/download:")
            page_key = f"page_{market}"
            signature = (search_query, min_rsi, min_vol_ratio, max_pe, partial_only, sort)
            if st.session_state.get(f"view_signature_{market}") != signature:
                st.session_state[page_key] = 1
                st.session_state[f"view_signature_{market}"] = signature
            if filtered.empty:
                st.info("No stocks match your view. Clear the search or loosen the sidebar filters.")
            else:
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
                    styled_view = (view.style
                                   .set_properties(subset=["ticker"], color="#4338ca", **{"font-weight": "600"})
                                   .set_properties(subset=["score"], color="#047857", **{"background-color": "#f0fdf9", "font-weight": "600"})
                                   .set_properties(subset=["volume_ratio"], color="#b45309", **{"background-color": "#fffbeb", "font-weight": "600"}))
                    st.dataframe(styled_view, width="stretch", hide_index=True, row_height=44,
                             column_config={
                                 "ticker": st.column_config.TextColumn("Ticker", width=140),
                                 "name": st.column_config.TextColumn("Company", width=220),
                                 "score": st.column_config.NumberColumn("Score", width=90, format="%.4f"),
                                 "price": st.column_config.NumberColumn(f"Price ({CURRENCY.get(market, '')})", width=110, format="%.2f"),
                                 "rsi": st.column_config.ProgressColumn("RSI", width=140, min_value=0, max_value=100, format="%.1f"),
                                 "volume_ratio": st.column_config.NumberColumn("Volume ratio (×)", width=140, format="%.2f", help="Compared with completed-session average. Partial-session ratios use a bounded projection."),
                                 "pe": st.column_config.NumberColumn("Trailing P/E", width=110, format="%.2f"),
                                 "session_partial": st.column_config.CheckboxColumn("Partial", width=80, help="Today's daily bar while the exchange is open. Volume is incomplete."),
                                 "bar_date": st.column_config.DateColumn("Session", width=128, format="DD MMM YYYY"),
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
                                     font_color="#212529", font_family="Source Sans, sans-serif", font_size=14,
                                     margin=dict(l=16, r=16, t=48, b=16),
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
    with performance_tab:
        from performance import render_performance
        render_performance(api_client, market)
    st.divider()
    st.caption("For research purposes. Not financial advice.")


if __name__ == "__main__":
    main()

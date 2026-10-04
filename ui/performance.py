"""Compact performance view. Data comes exclusively through the HTTP client."""
import pandas as pd
import streamlit as st


def render_performance(client, market):
    st.subheader("Signal performance")
    basis_label = st.radio("Returns", ["Gross", "Estimated net"], horizontal=True, key=f"perf_basis_{market}")
    return_basis = "net" if basis_label == "Estimated net" else "gross"
    controls = st.columns([1, 1, 1, 2])
    horizon = controls[0].selectbox("Holding sessions", [1, 5, 10], index=1, key=f"perf_horizon_{market}")
    start = controls[1].date_input("From signal date", value=None, key=f"perf_start_{market}")
    end = controls[2].date_input("To signal date", value=None, key=f"perf_end_{market}")
    if start and end and start > end:
        st.warning("Start date must not follow end date.")
        return
    data, error = client.get_performance(market=market, horizon=horizon, start=start, end=end, return_basis=return_basis)
    if error or not data:
        st.info(error or "Performance history is unavailable.")
        return
    if not data.get("supported"):
        st.info(data.get("message", "Performance is not available for this market."))
        return
    options = ["All strategies", *data["strategies"]]
    strategy = controls[3].selectbox("Strategy", options, key=f"perf_strategy_{market}",
        format_func=lambda value: "Legacy · settings unknown" if value == "legacy-unknown" else value)
    if strategy != "All strategies":
        data, error = client.get_performance(market=market,horizon=horizon,start=start,end=end,strategy=strategy,return_basis=return_basis)
        if error or not data:
            st.info(error or "No matching history.")
            return
    summary = data["summary"]
    fmt = lambda value, suffix: "—" if value is None else f"{value:.2f}{suffix}"
    for column, label, value in zip(st.columns(4),
        ["Excess-return hit rate", "Average excess return", "Evaluated / matured", "Price coverage"],
        [fmt(summary["hit_rate"], "%"), fmt(summary["mean_excess"], " pp"),
         f'{summary["valid"]} / {summary["matured"]}', fmt(summary["coverage"], "%")]):
        column.metric(label, value)
    st.caption(f'{summary["pending"]} pending · {summary["unresolved"]} unresolved · '
               f'{summary["excluded"]} excluded · {data["duplicates_removed"]} repeat captures removed. '
               f'Hit = excess return above zero. Returns: {basis_label.lower()}; benchmark remains gross.')
    with st.expander("Trading-cost assumptions"):
        model = data.get("cost_model", {})
        st.caption("1 basis point (bp) = 0.01%. Applied to entry cash outlay and exit proceeds, with slippage on both prices.")
        st.caption(f"Buy STT: {model.get('buy_stt_bps', 0):g} bp · Sell STT: {model.get('sell_stt_bps', 0):g} bp · "
                   f"Buy stamp duty: {model.get('stamp_bps', 0):g} bp. "
                   f"Each side: brokerage {model.get('brokerage_bps_each_side', 0):g} bp, "
                   f"other charges {model.get('other_cost_bps_each_side', 0):g} bp, "
                   f"slippage {model.get('slippage_bps_each_side', 0):g} bp.")
        st.caption(model.get("limitations", ""))
    if not summary["valid"]:
        st.info("No evaluated outcomes yet. Signals need completed holding sessions and exact stock/index prices.")
    elif summary["low_sample"]:
        st.warning("Small sample: treat these figures as descriptive, not evidence of a reliable edge.")
    split = st.radio("Group outcomes by", ["Score", "Volume ratio", "RSI band"], horizontal=True, key=f"perf_group_{market}")
    group = {"Score": "score", "Volume ratio": "volume_ratio", "RSI band": "rsi"}[split]
    buckets = pd.DataFrame(data["groups"][group])
    for key in ("hit_rate","mean_excess","median_excess","coverage","mean_stock","mean_benchmark"):
        buckets[key] = buckets[key].map(lambda value: "—" if pd.isna(value) else f"{value:.2f}")
    st.dataframe(buckets, hide_index=True, width="stretch",
        column_order=["bucket","hit_rate","mean_excess","valid","coverage","eligible","matured",
                      "pending","unresolved","excluded","median_excess","mean_stock","mean_benchmark","low_sample"], column_config={
        "bucket": "Bucket", "eligible": "Eligible", "valid": "Evaluated", "matured": "Matured",
        "hit_rate": "Hit rate (%)", "mean_excess": "Avg excess (pp)",
        "median_excess": "Median excess (pp)", "coverage": "Coverage (%)",
        "mean_stock": "Avg stock (%)", "mean_benchmark": "Avg index (%)",
        "pending": "Pending", "unresolved": "Unresolved", "excluded": "Excluded",
        "low_sample": "Small sample"})
    rows = pd.DataFrame(data["outcomes"])
    if not rows.empty:
        st.download_button("Export outcomes CSV", rows.to_csv(index=False), mime="text/csv",
                           file_name=f"signal_outcomes_{market}_{horizon}s.csv", on_click="ignore")
        with st.expander("Outcome audit records"):
            st.dataframe(rows, hide_index=True, width="stretch")
    job = data.get("job")
    if job:
        st.caption(f'Nightly evaluation: {job["status"]} · Last attempt: {job["started_at"]}')
        if job.get("error"):
            st.warning("Price provider issue: " + job["error"])
    else:
        st.caption(f'Nightly evaluation at {data["nightly_time"]} IST while the backend is running. '
                   'Missed runs catch up on restart.')
    if not data["enabled"]:
        st.info("Nightly outcome evaluation is disabled in backend settings.")
    elif not data.get("scheduler_running", True):
        st.info("Nightly jobs are paused in this API preview. Start the regular backend to enable scheduled evaluation.")
    st.caption("Entry: next session open after capture. Exit: holding-session close. "
               f'Benchmark: {data["benchmark"]} (default: Nifty 500). Split-adjusted price returns exclude cash dividends. '
               'Partial sessions are excluded; missing/delisted quotes remain unresolved. '
               'Signals from the same day are correlated; 5- and 10-session outcomes overlap. '
               'These are descriptive statistics, without a significance test. '
               'This history cannot test stocks that never qualified.')

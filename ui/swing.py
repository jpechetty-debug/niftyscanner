"""Compact Trade Ideas workspace. Every market/account/control read goes over HTTP."""
from datetime import date, datetime
from html import escape
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from ui.swing_charts import chart_figure, chart_context_captions


SETUPS = {"All setups": None, "Daily breakout": "DAILY_BREAKOUT_V1", "Trend pullback": "DAILY_PULLBACK_V1"}


def number(value, decimals=2, prefix=""):
    if value is None:
        return "—"
    try:
        return prefix+f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def label(value):
    names = {"strategy_validation": "Independent strategy validation", "benchmark:no_exact_vintage_prices": "Nifty 500 history unavailable",
             "historical_identity_unverified": "Historical security identity unverified", "historical_publication_unverified": "Historical publication unverified",
             "nifty500_benchmark_unavailable": "Nifty 500 benchmark unavailable", "point_in_time_universe_unverified": "Historical constituent membership unverified"}
    return names.get(value, str(value or "Unavailable").replace("_", " ").replace(":", " · ").strip().capitalize())


def display_time(value, *, include_time=False):
    try:
        instant = datetime.fromisoformat(str(value))
        if include_time:
            if instant.tzinfo is None:
                return "Time unavailable"
            return instant.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y · %H:%M IST")
        return instant.strftime("%d %b %Y")
    except (ValueError, TypeError):
        return "Unavailable"


def idea_card(plan, name=""):
    """Escaped compact HTML; unavailable size is never shown as an allocated position."""
    pairs = [("Reference close", number(plan.get("features", {}).get("close"), prefix="₹")),
             ("Entry range", f"{number(plan.get('entry_min'))}–{number(plan.get('entry_max'))}"),
             ("Invalidation", number(plan.get("stop"), prefix="₹")),
             ("Target scenario", number(plan.get("target"), prefix="₹")), ("Net reward/risk", number(plan.get("net_rr"))+"R")]
    stats = "".join(f'<div><span>{escape(k)}</span><strong>{escape(v)}</strong></div>' for k, v in pairs)
    setup = "Daily breakout" if plan.get("strategy") == "DAILY_BREAKOUT_V1" else "Trend pullback"
    return (f'<article class="swing-idea"><div class="swing-idea-top"><div><span class="swing-eyebrow">{escape(setup)}</span>'
            f'<h3>{escape(str(plan.get("ticker", "")))}</h3><p>{escape(name)}</p></div><span class="swing-badge">Research only</span></div>'
            f'<div class="swing-plan-stats">{stats}</div><div class="swing-idea-foot">Signal {escape(display_time(plan.get("session")))}'
            f' · Entry expires {escape(display_time(plan.get("expiry_session")))} · {escape(label(plan.get("lifecycle")))} · Size requires a reconciled account</div></article>')


def submit(client, path, body):
    """Freeze request/key across reruns and unknown HTTP outcomes."""
    if st.session_state.get("swing_pending_command"):
        st.warning("Resolve the saved submission before starting another command.")
        return
    request = {**body, "key": uuid4().hex}
    st.session_state["swing_pending_command"] = {"path": path, "body": request}
    retry_saved(client)


def retry_saved(client):
    pending = st.session_state["swing_pending_command"]
    result, error, retry = client.post_swing(pending["path"], pending["body"])
    if result is not None:
        st.session_state.pop("swing_pending_command", None)
        st.session_state["swing_notice"] = "Research request accepted. No broker order was placed."
        if result.get("status") == "QUEUED":
            st.session_state["swing_last_job"] = result["id"]
            st.session_state["swing_last_job_status"] = "QUEUED"
            st.session_state["swing_last_job_account"] = pending["body"].get("account_id")
        st.rerun()
    elif error and error.startswith("Submission status unknown"):
        st.rerun()  # make the saved retry control available immediately, without resubmitting
    else:
        st.session_state.pop("swing_pending_command", None)
        st.session_state["swing_error"] = (error or "Request not applied.")+(f" Retry after {retry}s." if retry else "")
        st.rerun()


@st.fragment(run_every=10)
def worker_strip(client):
    status, error = client.get_swing("operations")
    if not status:
        st.caption(error or "Worker status unavailable.")
        return
    controls = status["controls"]
    if st.session_state.get("swing_last_job"):
        job, _ = client.get_swing("paper/jobs/"+st.session_state["swing_last_job"])
        if job and job["status"] != st.session_state.get("swing_last_job_status"):
            st.session_state["swing_last_job_status"] = job["status"]
            st.rerun()
    cards = [("Ideas engine", "Research" if status["swing_enabled"] else "Disabled", "Completed daily bars"),
             ("Replay worker", "Running" if status["replay_worker_running"] else "Stopped", "Manual research jobs only"),
             ("Nightly outcomes", "Running" if status["outcomes_worker_running"] else "Disabled" if not status["outcomes_enabled"] else "Worker stopped", f"Configured {status['outcomes_time_ist']} IST"),
             ("New entries", "Paused" if controls["paused"] else "Opt-in required" if not status["replay_enabled"] else "Enabled", "Existing liabilities retained")]
    html = '<div class="swing-status-grid">'
    for title, value, detail in cards:
        html += f'<div class="swing-status"><span>{escape(title)}</span><strong>{escape(value)}</strong><small>{escape(detail)}</small></div>'
    st.markdown(html+'</div>', unsafe_allow_html=True)


def source_view(client, view, vintage):
    if not view:
        st.info("Select a real archive to inspect coverage and observed prices.")
        return
    source = view["source"]
    st.markdown("#### Source quality")
    left, right = st.columns([1, 1])
    with left:
        st.caption(f"Imported {display_time(source['imported_at'], include_time=True)} · Coverage session {display_time(source.get('coverage_session'))}")
        for issue in source.get("blockers", []):
            st.markdown("• "+label(issue))
    with right:
        st.caption("Nifty 500 benchmark")
        st.write(label(source["benchmark"].get("status")))
        st.caption(f"Close observations: {source['benchmark'].get('rows') or 0} · Open observations: {source['benchmark'].get('open_rows') or 0}")
        st.caption("Current constituent names are display labels, not historical membership evidence.")
    with st.expander("Coverage by stock"):
        if source.get("members"):
            st.dataframe(pd.DataFrame(source["members"]).rename(columns={"ticker": "Ticker", "current_series": "Series",
                "latest_price_session": "Latest observed session", "price_rows": "Observed sessions", "delivery_status": "Delivery context",
                "missing_price_session_count": "Missing sessions"}), hide_index=True, width="stretch", height=230)
        else:
            st.info("No per-stock coverage is available.")
    tickers = [m["ticker"] for m in source.get("members", []) if m.get("ticker")]
    ticker = st.selectbox("Inspect observed prices", tickers, index=None, placeholder="Select a stock", key="swing_source_ticker")
    if ticker:
        chart, error = client.get_swing("chart", vintage=vintage, ticker=ticker)
        if chart:
            missing = len(chart["missing_sessions"])
            st.caption(f"Price basis: {label(chart['price_basis'])} · {missing} missing {'session' if missing == 1 else 'sessions'} · Inspection only")
            figure = chart_figure(chart)
            if figure:
                st.plotly_chart(figure, width="stretch", theme=None, key="source_price_chart")
            else:
                st.info("No observed prices in this range. No older or substitute series was used.")
        else:
            st.info(error)
    with st.expander("Provenance and limitations"):
        st.code(vintage, language=None)
        for limitation in source.get("limitations", []):
            st.caption(limitation)


def rule_value(value):
    """Format recorded operands only; the UI never recalculates rule outcomes."""
    if value is None:
        return "Unavailable"
    if isinstance(value, (tuple, list)):
        return "["+", ".join(rule_value(item) for item in value)+"]"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return number(value, decimals=4)
    return str(value)


def rule_explanations_view(research, params):
    """Display evidence for matching and rejected stocks from the selected HTTP view."""
    explanations = research.get("rule_explanations", [])
    if not explanations:
        st.caption("Rule details unavailable for this response. Older saved plans are not reconstructed.")
        return
    explanations = [item for item in explanations if item.get("vintage") == params.get("vintage")]
    if not explanations:
        st.warning("Rule details do not match the selected source snapshot.")
        return
    with st.expander("Why stocks passed or were excluded"):
        tickers = sorted({item["ticker"] for item in explanations})
        selected = st.selectbox("Explain stock", tickers, key="swing_explanation_ticker")
        item = next(item for item in explanations if item["ticker"] == selected)
        st.caption(f"{label(item.get('outcome'))} · Decision {display_time(item.get('decision_at'), include_time=True)}"
                   f" · Signal session {display_time(item.get('data_session'))}")
        if item.get("reasons"):
            st.write("Recorded outcomes: "+" · ".join(label(reason) for reason in item["reasons"]))
        checks = [check for check in item.get("checks", [])
                  if check.get("strategy") is None or params.get("strategy") is None or check["strategy"] == params["strategy"]]
        if not checks:
            st.info("No technical rule values were calculated for this stock, or the selected stage was not reached. Review the recorded reasons and Source quality.")
            return
        rows = [{"Scope": label(check["strategy"]) if check.get("strategy") else "Shared",
                 "Rule": check["label"], "Observed": rule_value(check.get("observed")),
                 "Operator": check["operator"], "Threshold": rule_value(check.get("threshold")),
                 "Outcome": "Not evaluated" if check.get("passed") is None else "Pass" if check["passed"] else "Fail",
                 "Units": check.get("units", ""), "Explanation": check.get("note", "")} for check in checks]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Values are rounded for display; outcomes use unrounded backend values. Array operands compare in the same order. Each setup is independent; later stages may not be reached.")


def ideas_view(client, view, params, accounts):
    if not view:
        st.info("Import a real archive to inspect daily swing ideas.")
        return
    research = view["research"]
    plans = research.get("plans", [])
    names = research.get("names", {})
    tools = st.columns([3, 1])
    tools[0].caption(f"{len(plans)} ideas in this view · targets are rule-derived scenarios · no validated edge")
    tools[1].download_button("Export CSV", view.get("export_csv", ""), "swing-research.csv", "text/csv", key="swing_csv", on_click="ignore",
                             help="Exact server-generated snapshot shown here, including research status and missing gates.")
    if not plans:
        summary = research.get("view_summary", {})
        title = summary.get("title", "No ideas in this view")
        message = summary.get("message", "This response does not include a recorded reason for the empty view.")
        action = summary.get("next_action", "Refresh the research view for explanation metadata.")
        st.markdown('<section class="swing-empty"><div class="swing-empty-icon">⌁</div><h3>'+escape(title)+'</h3>'
                    '<p>'+escape(message)+'</p><p>'+escape(action)+'</p></section>', unsafe_allow_html=True)
    else:
        for plan in plans:
            st.markdown(idea_card(plan, names.get(plan["ticker"], "")), unsafe_allow_html=True)
        selected = st.selectbox("Plan detail", [p["id"] for p in plans], format_func=lambda key: next(p["ticker"]+" · "+label(p["strategy"]) for p in plans if p["id"] == key), key="swing_detail")
        plan = next(p for p in plans if p["id"] == selected)
        st.write(plan["rationale"])
        st.caption(f"Decision {plan['decision_at']} · Price basis {label(plan['price_basis'])} · Sector {plan['evidence'].get('sector') or 'Unverified'}")
        chart, error = client.get_swing("chart", vintage=params["vintage"], ticker=plan["ticker"])
        if chart and chart.get("observations"):
            context = next((item for item in research.get("chart_contexts", []) if item.get("plan_id") == plan["id"]), None)
            st.plotly_chart(chart_figure(chart, plan, context), width="stretch", theme=None, key="idea_price_chart")
            for caption in chart_context_captions(chart, plan, context):
                st.caption(caption)
        else:
            st.info(error or "Matching observed prices are unavailable.")
        st.caption("Missing gates: "+(" · ".join(label(g) for g in plan["missing_gates"]) or "None in this plan; strategy validation still required"))
    summary = research.get("view_summary")
    if summary:
        st.caption(f"{summary['total_candidates']} candidate plans before view filters · "
                   f"{summary['filtered_symbols']} stocks missed setup rules · {summary['failed_symbols']} stocks could not be evaluated")
    if research.get("missing_gates"):
        st.caption("Separate eligibility / validation requirements: "+" · ".join(label(g) for g in research["missing_gates"]))
    rule_explanations_view(research, params)
    with st.expander("Allocation preview"):
        account = st.selectbox("Reconciled research account", accounts, index=None, placeholder="Choose an existing account", key="swing_allocation_account")
        if account and st.button("Calculate hypothetical size", key="swing_allocation"):
            allocation, error = client.get_swing("allocation", account_id=account, **params)
            if allocation is not None:
                if allocation.get("allocations"):
                    st.dataframe(pd.DataFrame(allocation["allocations"]), hide_index=True, width="stretch")
                else:
                    st.info("No allocation available. "+" · ".join(allocation.get("missing_gates", [])))
            else:
                st.error(error)
        elif not accounts:
            st.caption("No research account exists. No capital or position size has been assumed.")


def paper_view(client, accounts, ops, vintage):
    st.markdown("#### Paper research")
    st.caption("Simulated cash flows and OHLC fill approximations. Unresolved paths remain separate.")
    account = st.selectbox("Research account", accounts, index=None, placeholder="Choose an account", key="swing_paper_account")
    state = None
    if account:
        state, error = client.get_swing("paper/accounts/"+account)
        if not state:
            st.error(error)
            return
        stats = st.columns(3)
        stats[0].metric("Settled cash", number(state.get("cash"), prefix="₹"))
        stats[1].metric("Reserved cash", number(state.get("reserved_cash"), prefix="₹"))
        stats[2].metric("Open holdings", len(state.get("positions", {})))
        performance, error = client.get_swing("paper/performance", account_id=account)
        if performance:
            st.caption(f"Resolved trades: {performance['resolved_trades']} · Unresolved records: {performance['unresolved_records']} · Net realised P/L: {number(performance['resolved_net_pnl'], prefix='₹')}")
            if performance.get("equity"):
                marks = pd.DataFrame(performance["equity"])
                figure = go.Figure()
                figure.add_scatter(x=marks["session"], y=pd.to_numeric(marks["nav"], errors="coerce"), name="Net research NAV", connectgaps=False, line_color="#4f46e5")
                figure.add_scatter(x=marks["session"], y=pd.to_numeric(marks["benchmark_nav"], errors="coerce"), name="Gross Nifty 500", connectgaps=False, line_color="#94a3b8")
                figure.update_layout(height=260, margin=dict(l=64, r=16, t=16, b=40), legend=dict(orientation="h"), yaxis=dict(automargin=True), xaxis=dict(automargin=True))
                st.plotly_chart(figure, width="stretch", theme=None, key="paper_nav")
            for title, rows in (("Open holdings", list(state.get("positions", {}).values())), ("Closed research trades", performance.get("closed", [])), ("Unresolved records", state.get("unresolved", []))):
                if rows:
                    with st.expander(title):
                        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info(error)
        with st.expander("Update research replay"):
            through = st.date_input("Through completed session", value=date.fromisoformat(ops["last_completed_session"]), max_value=date.fromisoformat(ops["last_completed_session"]), key="swing_through")
            manage = st.checkbox("Manage existing holdings only; suppress new entries", value=True, key="swing_manage_only")
            pending_job = st.session_state.get("swing_last_job_account") == account and st.session_state.get("swing_last_job_status") == "QUEUED"
            disabled = pending_job or ops.get("read_only_preview", False) or not vintage or not ops["replay_worker_running"] or (ops["controls"]["paused"] and not manage)
            if st.button("Queue research replay", disabled=disabled, key="swing_queue"):
                submit(client, "replay", {"account_id": account, "vintage": vintage, "through": str(through), "expected_version": state["version"], "management_only": manage})
            if disabled:
                st.caption("A selected source, enabled replay worker and compatible pause state are required.")
        known = [key for key, order in state.get("orders", {}).items() if order["lifecycle"] in {"CANDIDATE", "ARMED"}]
        if known:
            with st.expander("Cancel a known unfilled research order"):
                plan_id = st.selectbox("Unfilled plan", known, key="swing_cancel_plan")
                if st.button("Cancel unfilled research order", disabled=ops.get("read_only_preview", False) or not ops["replay_enabled"], key="swing_cancel"):
                    submit(client, "paper/orders/"+plan_id+"/cancel", {"account_id": account, "expected_version": state["version"], "reason": "Explicit user cancellation from Trade Ideas"})
    else:
        st.info("No account selected. No capital, fills or returns have been invented.")
    with st.expander("Create an explicitly funded research account"):
        with st.form("swing_fund_form"):
            identifier = st.text_input("New account ID", placeholder="Choose an identifier", key="swing_account_id")
            capital = st.text_input("Research capital (INR)", placeholder="Enter an explicit positive amount", key="swing_account_capital")
            seed = st.date_input("Completed starting session", value=None, max_value=date.fromisoformat(ops["last_completed_session"]), key="swing_seed")
            create = st.form_submit_button("Create research account", disabled=ops.get("read_only_preview", False) or not ops["replay_enabled"] or ops["controls"]["paused"])
        if create:
            if identifier and capital and seed:
                submit(client, "paper/accounts", {"account_id": identifier, "capital_inr": capital, "session": str(seed)})
            else:
                st.error("Enter the account ID, capital and completed starting session.")
    if st.session_state.get("swing_last_job"):
        job, error = client.get_swing("paper/jobs/"+st.session_state["swing_last_job"])
        st.caption("Last replay: "+(job["status"] if job else error))


def forward_view(client):
    st.markdown("#### Forward paper reconciliation")
    st.caption("Record actual sessions before considering promotion. OHLC references are compared with simulated fills; they are not broker confirmations.")
    campaigns, error = client.get_swing("forward/campaigns")
    if campaigns is None:
        st.info(error or "Forward monitor unavailable.")
        return
    if not campaigns:
        st.info("No prospective campaign registered. A future window, explicit research account and at least 30 actual trading sessions are required.")
        st.caption("Register with scripts/swing_forward.py. After source import and completed paper replay, run capture once per session before the next open. Missed sessions cannot be backfilled.")
        return
    selected = st.selectbox("Forward campaign", [r["campaign"] for r in campaigns], key="swing_forward_campaign")
    report = next(r for r in campaigns if r["campaign"] == selected)
    a, b, c = st.columns(3)
    a.metric("Captured sessions", report["captured_sessions"])
    b.metric("Reconciled sessions", report["reconciled_sessions"])
    c.metric("Minimum window", report["minimum_sessions"])
    st.caption(f"{display_time(report['start'])}–{display_time(report['end'])} · Research only · Promotion blocked pending independent validation")
    st.caption(f"Last new-plan session: {display_time(report['signal_end'])}. Use management-only replay during the declared wind-down.")
    if report["failures"]:
        st.dataframe(pd.DataFrame({"Unresolved gate": [label(f) for f in report["failures"]]}), hide_index=True, width="stretch", height=180)
    if report["missing_sessions"]:
        st.warning("Missing actual session captures: "+", ".join(report["missing_sessions"]))
    comparisons = [row for day in report["days"] for row in day["comparisons"]]
    if comparisons:
        with st.expander("Planned entries versus observed references"):
            st.dataframe(pd.DataFrame(comparisons), hide_index=True, width="stretch")
    with st.expander("Forward audit manifest"):
        st.json(report)


def validation_view(client):
    forward_view(client)
    reports, error = client.get_swing("validation/reports")
    st.markdown("#### Independent validation")
    if reports is None:
        st.error(error)
    elif not reports:
        st.info("No real evaluation report is registered. Strategies remain research only.")
        st.caption("Source gaps must be resolved before a future preregistered holdout can establish evidence. This page never opens a holdout.")
    else:
        selected = st.selectbox("Frozen evaluation report", [r["id"] for r in reports], format_func=lambda key: next(r["study_id"]+" · "+r["phase"]+" · "+label(r["model_status"]) for r in reports if r["id"] == key), key="swing_report")
        report, error = client.get_swing("validation/reports/"+selected)
        if report:
            payload = report["payload"]
            st.caption(f"Authentication: {report['authentication']} · {len(payload['failures'])} failed gates · Read-only recommendation")
            st.write(payload["model_status"])
            if payload["failures"]:
                st.dataframe(pd.DataFrame({"Failed validation gate": payload["failures"]}), hide_index=True, width="stretch", height=220)
            with st.expander("Frozen metrics and manifest"):
                st.json(report)
        else:
            st.error(error)


def worker_timing_view(ops):
    """Display measured API values; unknown queue ages/durations stay unavailable."""
    st.markdown("##### Worker timing")
    metrics = ops.get("worker_timings")
    if not metrics:
        st.caption("Worker timing unavailable in this response.")
        return
    st.caption(f"Observed: {metrics.get('observed_at', 'Unavailable')} · Use Refresh research view to update this snapshot.")
    rows = []
    for worker in metrics.get("workers", []):
        attempt = worker.get("active") or worker.get("last") or {}
        rows.append({"Worker": worker["worker"], "State": worker["state"],
            "Queue age (s)": attempt.get("queue_age_sec"), "Queue timestamp source": attempt.get("queue_source", "Unavailable"),
            "Lock wait (s)": attempt.get("current_wait_sec") if worker["state"] == "waiting" else attempt.get("lock_wait_sec"),
            "Task duration (s)": attempt.get("duration_sec"), "Max task duration (s)": worker.get("max_duration_sec"),
            "Max lock wait (s)": worker.get("max_lock_wait_sec"), "Preflight failures": worker.get("preflight_failures", 0),
            "Busy deferrals": worker["busy_deferrals"], "Attempts": worker["attempts"],
            "Last outcome": (worker.get("last") or {}).get("outcome", "Unavailable"),
            "Phase": attempt.get("phase", "Unavailable"), "Last progress": worker.get("last_progress_at")})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    queue = metrics.get("replay_queue", {})
    st.caption(f"Pending replay jobs: {number(queue.get('pending'), decimals=0)} · "
               f"Oldest queue age: {number(queue.get('oldest_age_sec'))} s · {label(queue.get('status'))}")
    if queue.get("age_status") == "timestamp_unavailable":
        st.caption("Replay queue timestamp is unavailable or invalid; its age is not assumed to be zero.")
    st.caption("Queue age freezes at task start. Lock wait measures acquisition only; busy deferrals count skipped worker polls, not queued jobs. "
               "Task duration includes the existing work under the lock. Timings reset with the scheduler process; "
               "no sample means unavailable. Lifecycle queue age is unavailable because enqueue timestamps are not stored.")


def operations_view(client, ops, vintage):
    st.markdown("#### Operations")
    control = ops["controls"]
    st.caption(f"Control revision {control['revision']} · Scheduler {'running' if ops['scheduler_running'] else 'stopped'} · Automatic idea generation is not scheduled")
    worker_timing_view(ops)
    columns = st.columns(3)
    preview = ops.get("read_only_preview", False)
    if columns[0].button("Resume new work" if control["paused"] else "Pause new work", disabled=preview, width="stretch", key="swing_pause"):
        submit(client, "operations", {"expected_revision": control["revision"], "paused": not control["paused"]})
    if columns[1].button("Pin selected source", disabled=preview or not vintage or control.get("selected_vintage") == vintage, width="stretch", key="swing_pin"):
        submit(client, "operations", {"expected_revision": control["revision"], "selected_vintage": vintage})
    if columns[2].button("Pause lifecycle alerts" if not control["alerts_paused"] else "Enable lifecycle alerts", disabled=preview or not ops["webhook_configured"] or not ops["swing_enabled"], width="stretch", key="swing_alerts"):
        submit(client, "operations", {"expected_revision": control["revision"], "alerts_paused": not control["alerts_paused"]})
    st.caption("Pause stops new capture/entries and queued new work. Existing holdings and unknown liabilities are retained. Management-only replay remains explicit.")
    if not ops["replay_enabled"]:
        st.caption("The global swing/replay opt-in flags are still required; resuming controls does not enable them.")
    if not ops["webhook_configured"]:
        st.caption("Lifecycle delivery is off. No dedicated destination is configured.")
    with st.expander("Rollback control selection"):
        history = ops.get("history", [])
        revisions = [0]+[h["revision"] for h in history if h["revision"] < control["revision"]]
        target = st.selectbox("Earlier control revision", revisions, key="swing_rollback_revision")
        st.caption("Restores the source/control selection and pauses new work/alerts. Plans, cash and fills are never rewritten.")
        if st.button("Restore controls and pause", disabled=preview or control["revision"] == 0, key="swing_rollback"):
            submit(client, "operations", {"expected_revision": control["revision"], "rollback_revision": target})
    if ops.get("outbox"):
        st.dataframe(pd.DataFrame(ops["outbox"])[["ticker", "session", "lifecycle", "status", "attempt"]], width="stretch", hide_index=True)
        uncertain = [a for a in ops["outbox"] if a["status"] in {"UNKNOWN", "REJECTED"} and a.get("destination_matches", True)]
        if uncertain:
            with st.expander("Review uncertain delivery"):
                selected = st.selectbox("Delivery event", [a["event_id"] for a in uncertain], key="swing_alert_retry")
                st.caption("The same event ID is retained. The recipient must honor Idempotency-Key to prevent a duplicate after network uncertainty.")
                if st.button("Retry reviewed event", disabled=preview, key="swing_alert_retry_button"):
                    submit(client, "operations/alerts/"+selected+"/retry", {})


def render_swing(client, header):
    ops, error = client.get_swing("operations")
    header("NSE", ops is not None)
    st.markdown('<div class="swing-heading"><div><h1>Trade Ideas</h1><p>Daily setups, explicit risk and auditable research.</p></div><span class="swing-badge">Research only · NSE cash</span></div>', unsafe_allow_html=True)
    if not ops:
        st.error(error or "Research API unavailable.")
        return
    if ops.get("read_only_preview"):
        st.caption("Read-only preview · scans, account changes, replay and outbound alerts are disabled.")
    if st.session_state.get("swing_notice"):
        st.success(st.session_state.pop("swing_notice"))
    if st.session_state.get("swing_error"):
        st.error(st.session_state.pop("swing_error"))
    if st.session_state.get("swing_pending_command"):
        st.warning("A submission has an unknown result. Keep its saved key until it is resolved.")
        st.code(st.session_state["swing_pending_command"]["body"]["key"], language=None)
        if st.button("Retry saved submission", key="swing_retry_saved"):
            retry_saved(client)
    vintages, discovery_error = client.get_swing("vintages")
    vintages = vintages or []
    with st.sidebar:
        st.markdown("**Trade Ideas filters**")
        options = [v["vintage"] for v in vintages]
        pinned = ops["controls"].get("selected_vintage")
        index = options.index(pinned) if pinned in options else 0 if options else None
        vintage = st.selectbox("Source snapshot", options, index=index, format_func=lambda value: next(v["imported_at"][:10]+" · "+value[:8] for v in vintages if v["vintage"] == value), placeholder="No real archive available", key="swing_vintage")
        setup = st.selectbox("Setup", list(SETUPS), key="swing_setup")
        search = st.text_input("Search ideas", placeholder="Ticker or company", key="swing_search")
        sector = st.text_input("Sector tag", placeholder="Optional exact sector", key="swing_sector")
        st.caption("View filters do not change strategy rules or ranks.")
        st.button("Refresh research view", width="stretch", key="swing_refresh")
        st.caption("Completed daily bars · Delayed data · No broker execution")
    worker_strip(client)
    params = {"vintage": vintage, "strategy": SETUPS[setup], "sector": sector.strip() or None, "search": search.strip()}
    view, view_error = client.get_swing("workspace", **params) if vintage else (None, discovery_error)
    if view_error:
        st.error(view_error)
    if not ops["swing_enabled"]:
        st.info("Swing research is disabled. Source inspection and existing ledger reads remain available.")
    if view:
        summary = view["source"]["summary"]
        stats = [("Ideas in view", len(view["research"]["plans"])), ("Current universe", summary.get("universe", 0)),
                 ("Complete histories", summary.get("complete_trailing_price_histories", 0)), ("Source blockers", len(view["source"]["blockers"]))]
        st.markdown('<div class="swing-metrics">'+"".join(f'<div><span>{escape(title)}</span><strong>{value:,}</strong></div>' for title, value in stats)+'</div>', unsafe_allow_html=True)
        if view["source"]["stale"]:
            st.warning("This archive has incomplete or stale coverage. It cannot support an executable swing recommendation.")
    accounts, accounts_error = client.get_swing("paper/accounts")
    accounts = accounts or []
    tabs = st.tabs(["Trade ideas", "Source quality", "Paper research", "Validation", "Operations"])
    with tabs[0]:
        ideas_view(client, view, params, accounts)
    with tabs[1]:
        source_view(client, view, vintage)
    with tabs[2]:
        if accounts_error:
            st.info(accounts_error)
        paper_view(client, accounts, ops, vintage)
    with tabs[3]:
        validation_view(client)
    with tabs[4]:
        operations_view(client, ops, vintage)
    st.caption("Research only. Targets are scenarios, not forecasts. Not financial advice.")

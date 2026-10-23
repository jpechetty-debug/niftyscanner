"""HTTP-payload chart rendering; no indicator calculation or provider access."""
from datetime import date, datetime
from math import isfinite

import plotly.graph_objects as go
from plotly.subplots import make_subplots

KNOWN_BASES = {"raw", "split_adjusted", "cash_dividend_adjusted", "total_return"}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _compatible(payload, plan):
    return bool(plan and payload.get("price_basis") in KNOWN_BASES
        and payload["price_basis"] == plan.get("price_basis")
        and payload.get("ticker") == plan.get("ticker")
        and payload.get("vintage") == plan.get("evidence", {}).get("vintage")
        and all(r.get("ticker", plan["ticker"]) == plan["ticker"]
                and r.get("price_basis", plan["price_basis"]) == plan["price_basis"]
                for r in payload.get("observations", [])))


def matched_breakout_context(payload, plan, context):
    """Fail closed on incomplete metadata or any cross-plan/chart identity."""
    if not _compatible(payload, plan) or plan.get("strategy") != "DAILY_BREAKOUT_V1" or not isinstance(context, dict):
        return None
    bindings = {"plan_id": plan.get("id"), "ticker": plan.get("ticker"),
                "vintage": plan.get("evidence", {}).get("vintage"), "price_basis": plan.get("price_basis"),
                **{key: plan.get(key) for key in ("session", "decision_at", "config_hash", "data_hash")}}
    if any(value is None or context.get(key) != value for key, value in bindings.items()):
        return None
    try:
        resistance, volume, extension = (context[key] for key in ("resistance", "volume", "extension"))
        signal = date.fromisoformat(context["session"])
        if datetime.fromisoformat(context["decision_at"]).tzinfo is None:
            return None
        for window in (resistance, volume):
            start, end = (date.fromisoformat(window[key]) for key in ("start", "end"))
            if not start <= end < signal or not isinstance(window["sessions"], int) or isinstance(window["sessions"], bool) or window["sessions"] < 1:
                return None
        values = [resistance["level"], *(volume[key] for key in
            ("average_volume", "signal_volume", "relative_volume", "minimum_relative_volume")),
            *(extension[key] for key in ("atr", "period", "distance", "multiple", "maximum_multiple",
                                       "maximum_close", "ema_fast_multiple", "ema_fast_maximum_multiple"))]
        if not all(_finite(value) for value in values) or min(resistance["level"], volume["average_volume"], extension["atr"], extension["period"]) <= 0:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return context


def chart_context_captions(payload, plan, context=None):
    """Explain recorded values without deriving indicators from displayed bars."""
    if not plan or plan.get("strategy") != "DAILY_BREAKOUT_V1":
        return ()
    if not _compatible(payload, plan):
        return ("Breakout overlays unavailable: chart ticker, vintage or known price basis does not match this plan.",)
    context = matched_breakout_context(payload, plan, context)
    if context is None:
        return ("Recorded breakout chart context unavailable for this response. Existing plan levels are shown where compatible.",)
    r, v, e = (context[key] for key in ("resistance", "volume", "extension"))
    return (f"Recorded resistance ₹{r['level']:,.2f} · Prior {r['sessions']} sessions ({r['start']} to {r['end']}); signal excluded.",
        f"Recorded volume baseline {v['average_volume']:,.0f} shares · Prior {v['sessions']} sessions ({v['start']} to {v['end']}); signal excluded. "
        f"Signal {v['signal_volume']:,.0f} shares · {v['relative_volume']:.2f}× baseline (minimum {v['minimum_relative_volume']:.2f}×).",
        f"Signal ATR({e['period']}) ₹{e['atr']:,.2f} · Close − resistance {e['multiple']:.2f} ATR / {e['maximum_multiple']:.2f} ATR maximum. "
        f"Close − fast EMA {e['ema_fast_multiple']:.2f} ATR / {e['ema_fast_maximum_multiple']:.2f} ATR maximum. "
        "Levels are recorded for the signal session, not moving indicators or entry instructions.")


def chart_figure(payload, plan=None, context=None):
    rows = payload.get("observations", [])
    if not rows:
        return None
    recorded = matched_breakout_context(payload, plan, context)
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.73, 0.27], vertical_spacing=0.08) if recorded else go.Figure()
    candles = go.Candlestick(x=[r["session"] for r in rows], open=[r.get("open") for r in rows],
        high=[r.get("high") for r in rows], low=[r.get("low") for r in rows], close=[r.get("close") for r in rows],
        increasing_line_color="#059669", decreasing_line_color="#64748b", name="Observed prices", showlegend=False)
    if recorded:
        figure.add_trace(candles, row=1, col=1)
    else:
        figure.add_trace(candles)
    if _compatible(payload, plan):
        for key, color in (("stop", "#dc2626"), ("target", "#059669"), ("entry_min", "#4f46e5"), ("entry_max", "#4f46e5")):
            if _finite(plan.get(key)):
                figure.add_shape(type="line", name=key, xref="x", yref="y", x0=plan["session"],
                    x1=max(rows[-1]["session"], plan["expiry_session"]), y0=plan[key], y1=plan[key], line=dict(color=color, dash="dot"))
    if recorded:
        r, v, e = (recorded[key] for key in ("resistance", "volume", "extension"))
        first, last, signal = rows[0]["session"], rows[-1]["session"], plan["session"]
        start, end = max(first, r["start"]), min(last, r["end"])
        if start <= end:
            figure.add_shape(type="rect", name="Recorded resistance window", xref="x", yref="y domain",
                x0=start, x1=end, y0=0, y1=1, fillcolor="rgba(124,58,237,0.07)", line_width=0, layer="below")
        start, end = max(first, r["start"]), min(last, signal)
        if start <= end:
            for value, name, color in ((r["level"], "Recorded resistance", "#7c3aed"),
                                       (e["maximum_close"], "Breakout close ceiling", "#b45309")):
                figure.add_trace(go.Scatter(x=[start, end], y=[value, value], mode="lines", name=name,
                    line=dict(color=color, dash="dash"), hovertemplate=name+": ₹%{y:,.2f}<extra></extra>"), row=1, col=1)
            figure.add_shape(type="rect", name="Breakout ATR allowance", xref="x", yref="y", x0=start, x1=end,
                y0=r["level"], y1=e["maximum_close"], fillcolor="rgba(245,158,11,0.06)", line_width=0, layer="below")
        # A recorded marker is only shown where the signal itself is in view.
        if any(row["session"] == signal for row in rows):
            figure.add_trace(go.Scatter(x=[signal], y=[plan["features"]["close"]], mode="markers", name="Signal close",
                marker=dict(size=8, color="#4f46e5")), row=1, col=1)
        figure.add_trace(go.Bar(x=[row["session"] for row in rows], y=[row.get("volume") for row in rows],
            name="Observed volume", marker_color="#cbd5e1", showlegend=False,
            hovertemplate="%{x}<br>Observed volume: %{y:,.0f}<extra></extra>"), row=2, col=1)
        start, end = max(first, v["start"]), min(last, signal)
        if start <= end:
            figure.add_trace(go.Scatter(x=[start, end], y=[v["average_volume"]]*2, mode="lines", name="Recorded volume baseline",
                line=dict(color="#0f766e", dash="dash"), hovertemplate="Recorded baseline: %{y:,.0f} shares<extra></extra>"), row=2, col=1)
        figure.update_yaxes(title_text="Volume (shares)", rangemode="tozero", row=2, col=1)
        figure.update_yaxes(title_text="Observed price (₹)", row=1, col=1)
    else:
        figure.update_yaxes(title_text="Observed price (₹)")
    figure.update_layout(height=480 if recorded else 300, margin=dict(l=64, r=16, t=64 if recorded else 16, b=40),
        paper_bgcolor="#ffffff", plot_bgcolor="#ffffff", font=dict(color="#334155", size=12),
        showlegend=bool(recorded), legend=dict(orientation="h", y=1.14, x=0, font=dict(size=11)),
        hovermode="x unified" if recorded else "closest")
    figure.update_xaxes(automargin=True, rangeslider_visible=False)
    figure.update_yaxes(automargin=True)
    return figure

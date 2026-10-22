"""Descriptive portfolio metrics and signal-date moving-block uncertainty."""
import math
import numpy as np


def clustered_interval(panels, block, gates):
    """Resample complete date clusters, preserving zero-trade dates and window gaps."""
    rng = np.random.default_rng(gates.seed)
    totals = np.zeros((gates.bootstrap_replicates, 3))  # pnl, R, count
    periods, dates = 0, 0
    for panel in panels:
        sessions = panel["sessions"]
        n = len(sessions)
        if not n:
            continue
        values = np.zeros((n, 3))
        lookup = {s: i for i, s in enumerate(sessions)}
        for trade in panel["trades"]:
            values[lookup[trade["signal_session"]]] += (trade["pnl"], trade["r"], 1)
        length = min(n, block)
        periods += n // block
        dates += n
        prefix = np.vstack([np.zeros(3), values.cumsum(axis=0)])
        lengths = np.array([length]* (n//length) + ([n % length] if n % length else []))
        # At most replicates x blocks, never replicates x trades.
        starts = rng.integers(0, n-length+1, size=(gates.bootstrap_replicates, len(lengths)))
        totals += (prefix[starts+lengths] - prefix[starts]).sum(axis=1)
    valid = totals[:, 2] > 0
    estimates = totals[valid, :2] / totals[valid, 2, None]
    bounds = np.quantile(estimates, [gates.alpha/2, 1-gates.alpha/2], axis=0).tolist() if valid.any() else None
    # Sparse replicates invalidate a significance claim; don't condition away empty resamples.
    return {"method": "signal-date moving-block percentile bootstrap", "alpha": gates.alpha,
            "seed": gates.seed, "replicates": gates.bootstrap_replicates, "block_sessions": block,
            "nonoverlapping_periods": periods, "signal_calendar_sessions": dates,
            "empty_replicates": int((~valid).sum()),
            "expectancy_inr": [bounds[0][0], bounds[1][0]] if bounds else None,
            "expectancy_r": [bounds[0][1], bounds[1][1]] if bounds else None}


def drawdown(values):
    peak, maximum, duration, longest = values[0], 0., 0, 0
    for value in values[1:]:
        peak = max(peak, value)
        maximum = max(maximum, 1-value/peak)
        duration = duration+1 if value < peak else 0
        longest = max(longest, duration)
    return maximum, longest


def baseline_curve(panel, fraction):
    """Constant index/cash weights; self-financing rebalance with frozen S2 fees."""
    initial = panel["capital"]
    buy_fee, sell_fee = panel["buy_multiplier"]-1, 1-panel["sell_multiplier"]
    wealth = initial/(1+fraction*buy_fee)
    asset, cash = wealth*fraction, wealth*(1-fraction)
    previous_index = initial
    result = []
    for day in panel["daily"]:
        asset *= day["benchmark_nav"]/previous_index
        total = asset+cash
        if fraction*total >= asset:
            delta = (fraction*total-asset)/(1+fraction*buy_fee)
            asset += delta
            cash -= delta*(1+buy_fee)
        else:
            delta = (asset-fraction*total)/(1-fraction*sell_fee)
            asset -= delta
            cash += delta*(1-sell_fee)
        result.append(asset+cash)
        previous_index = day["benchmark_nav"]
    if result:
        result[-1] = cash + asset*panel["sell_multiplier"]  # terminal liquidation
    return result


def summarize(panels, gates, block):
    trades = [t for p in panels for t in p["trades"]]
    values = [t["pnl"] for t in trades]
    wins, losses = [v for v in values if v > 0], [v for v in values if v < 0]
    chain, index_chain, baseline_chain, diagnostic = [1.], [1.], [1.], [1.]
    exposures, participation, turnover, holdings = [], [], 0., []
    for p in panels:
        capital = p["capital"]
        initial, prior_idx, prior_exp = chain[-1], capital, 0.
        idx_start, base_start, diag_start = index_chain[-1], baseline_chain[-1], diagnostic[-1]
        baseline = baseline_curve(p, gates.baseline_exposure)
        for i, day in enumerate(p["daily"]):
            chain.append(initial*day["nav"]/capital)
            index_chain.append(idx_start*day["benchmark_nav"]/capital)
            baseline_chain.append(base_start*baseline[i]/capital)
            diag_start *= 1 + prior_exp*(day["benchmark_nav"]/prior_idx-1)
            diagnostic.append(diag_start)
            exposures.append(day["exposure"])
            prior_idx, prior_exp = day["benchmark_nav"], day["exposure"]
        participation.extend(t["participation"] for t in p["trades"])
        turnover += sum(t["buy_gross"]+t["sell_gross"] for t in p["trades"])/capital
        holdings.extend(t["holding_sessions"] for t in p["trades"])
    max_dd, duration = drawdown(chain)
    attempts = sum(p["attempts"] for p in panels)
    filled = sum(p["filled"] for p in panels)
    interval = clustered_interval(panels, block, gates)
    cost_stress = [{"extra_bps_per_side": bps, "mean_net_pnl": float(np.mean([
        t["pnl"]-bps/10000*(t["buy_gross"]+t["sell_gross"]) for t in trades])) if trades else None,
        "scope": "Fixed filled cash-flow sensitivity; no reallocation or alternate fill claim"} for bps in gates.extra_cost_bps]
    return {"trades": len(trades), "wins": len(wins), "losses": len(losses), "flat": len(values)-len(wins)-len(losses),
            "win_rate": len(wins)/len(values) if values else None,
            "mean_net_pnl": float(np.mean(values)) if values else None,
            "mean_net_r": float(np.mean([t["r"] for t in trades])) if trades else None,
            "pnl_quantiles": np.quantile(values, [0, .25, .5, .75, 1]).tolist() if values else None,
            "payoff_ratio": float(np.mean(wins)/-np.mean(losses)) if wins and losses else None,
            "net_return": chain[-1]-1, "gross_index_return": index_chain[-1]-1,
            "constant_exposure_index_net_return": baseline_chain[-1]-1,
            "cash_return": 0., "lagged_exposure_index_gross_return": diagnostic[-1]-1,
            "baseline_exposure": gates.baseline_exposure,
            "excess_vs_constant_baseline": chain[-1]-baseline_chain[-1],
            "max_drawdown": max_dd, "underwater_sessions": duration,
            "max_exposure": max([*exposures, *(p.get("entry_exposure", 0.) for p in panels)], default=0.), "mean_exposure": float(np.mean(exposures)) if exposures else None,
            "max_adv_participation": max(participation, default=0.), "turnover_per_starting_nav": turnover,
            "mean_holding_sessions": float(np.mean(holdings)) if holdings else None,
            "sectors": sorted({t["sector"] for t in trades if t["sector"]}), "regimes": "Not independently classified",
            "attempts": attempts, "filled": filled, "fill_coverage": filled/attempts if attempts else 0.,
            "daily_nav_coverage": sum(len(p["daily"]) for p in panels)/max(1, sum(len(p["sessions"]) for p in panels)),
            "uncertainty": interval, "cost_stress": cost_stress,
            "window_count": len(panels), "curve": chain,
            "notice": "Disjoint normalized windows; embargo gaps omitted, not forward-filled. Gross price index versus net strategy."}


def metric_failures(metrics, gates):
    failures = []
    for ok, reason in (
        (metrics["trades"] >= gates.min_trades, "insufficient_trades"),
        (metrics["uncertainty"]["nonoverlapping_periods"] >= gates.min_periods, "insufficient_independent_periods"),
        (len(metrics["sectors"]) >= gates.min_sectors, "insufficient_sectors"),
        (metrics["net_return"] > 0, "nonpositive_net_return"),
        (metrics["excess_vs_constant_baseline"] >= 0, "baseline_underperformance"),
        (metrics["max_drawdown"] <= gates.max_drawdown, "drawdown_limit"),
        (metrics["max_exposure"] <= gates.max_exposure, "exposure_limit"),
        (metrics["max_adv_participation"] <= gates.max_participation, "capacity_limit"),
        (metrics["fill_coverage"] >= gates.min_fill_coverage, "fill_coverage"),
        (metrics["daily_nav_coverage"] == 1, "nav_coverage"),
        (metrics["uncertainty"]["empty_replicates"] == 0, "sparse_bootstrap"),
        (all(s["mean_net_pnl"] is not None and s["mean_net_pnl"] > 0 for s in metrics["cost_stress"]), "cost_stress")):
        if not ok:
            failures.append(reason)
    for key in ("expectancy_inr", "expectancy_r"):
        bounds = metrics["uncertainty"][key]
        if bounds is None or not math.isfinite(bounds[0]) or bounds[0] <= 0:
            failures.append("nonpositive_lower_"+key)
    return failures

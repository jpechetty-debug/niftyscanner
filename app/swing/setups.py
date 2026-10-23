"""Independent completed-daily-bar hypotheses with bounded cost-aware entries."""
from dataclasses import asdict, replace
from datetime import date, datetime
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.core.config import Settings
from app.providers.swing_archive import last_completed
from app.swing.indicators import atr, ema
from app.swing.explanations import record_check
from app.swing.models import DailyPrice, utc
from app.swing.plans import Costs, Evidence, Evaluation, Features, Plan, Rejection, priority


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str, allow_nan=False).encode()).hexdigest()


def implementation_hash() -> str:
    root = Path(__file__).parent
    return digest({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                   for name in ("config.py", "plans.py", "indicators.py", "setups.py", "risk.py", "service.py", "explanations.py", "charts.py")})


def configuration(config: Settings):
    return tuple(sorted((k, v) for k, v in config.model_dump().items()
                        if (k.startswith("SWING_") and not k.endswith("PATH") and k != "SWING_ARTIFACT_DIR")
                        or k.startswith("PERFORMANCE_") and k.endswith("BPS") or k == "NSE_EXTRA_HOLIDAYS"))


def costs(config: Settings) -> Costs:
    names = ("PERFORMANCE_BUY_STT_BPS", "PERFORMANCE_SELL_STT_BPS", "PERFORMANCE_STAMP_BPS",
             "PERFORMANCE_BROKERAGE_BPS", "PERFORMANCE_OTHER_COST_BPS", "PERFORMANCE_SLIPPAGE_BPS")
    p = {key: getattr(config, key) for key in names}
    buy = (p[names[0]] + p[names[2]] + p[names[3]] + p[names[4]]) / 10000
    sell = (p[names[1]] + p[names[3]] + p[names[4]]) / 10000
    slip = p[names[5]] / 10000
    return Costs((1 + slip) * (1 + buy), (1 - slip) * (1 - sell), tuple(sorted(p.items())))


def payoff(entry: float, stop: float, target: float, model: Costs) -> tuple[float, float]:
    risk = entry * model.buy_multiplier - stop * model.sell_multiplier
    reward = target * model.sell_multiplier - entry * model.buy_multiplier
    return risk, reward / risk if risk > 0 else float("-inf")


def required_sessions(cfg: Settings) -> int:
    rs = [int(x) for x in cfg.SWING_RS_LOOKBACKS.split(",")]
    return max(cfg.SWING_MIN_HISTORY_SESSIONS, 2 * cfg.SWING_EMA_SLOW,
               cfg.SWING_EMA_MID + cfg.SWING_SLOPE_SESSIONS, cfg.SWING_ATR_PERIOD,
               max(rs) + 1, cfg.SWING_VOLUME_LOOKBACK + cfg.SWING_PULLBACK_LOOKBACK + 1,
               cfg.SWING_BREAKOUT_LOOKBACK + 1, cfg.SWING_OVERHEAD_LOOKBACK + 1)


def prepare_history(ticker, rows, benchmark, calendar, now, cfg):
    """Select only completed prefix; require exact synchronized trailing sessions."""
    completed = date.fromisoformat(last_completed(calendar, utc(now)))
    n = required_sessions(cfg)
    sessions = calendar.calendar.sessions
    expected = [x.date() for x in sessions[sessions <= pd.Timestamp(completed)][-n:]]
    selected = []
    for data, symbol, instrument in ((rows, ticker, "equity"), (benchmark, cfg.SWING_BENCHMARK, "index")):
        prefix = [r for r in data if r.session <= completed]
        dates = [r.session for r in prefix]
        if dates != sorted(set(dates)):
            raise ValueError("duplicate_or_unsorted_sessions")
        prefix = prefix[-n:]
        if len(prefix) != n or [r.session for r in prefix] != expected:
            raise ValueError("insufficient_or_missing_exact_sessions")
        if any(r.ticker != symbol or r.instrument != instrument for r in prefix):
            raise ValueError("instrument_or_ticker_mismatch")
        bases = {r.price_basis for r in prefix}
        if len(bases) != 1 or bases & {"unknown", "provider_adjusted_unknown"}:
            raise ValueError("price_basis_unresolved_or_mixed")
        if instrument == "equity" and len({(r.isin, r.series) for r in prefix}) != 1:
            raise ValueError("unreconciled_identity_transition")
        selected.append(tuple(prefix))
    return selected


def evaluate(ticker: str, rows: tuple[DailyPrice, ...], benchmark: tuple[DailyPrice, ...],
             evidence: Evidence, config: Settings, calendar, decision_at: datetime) -> Evaluation:
    """Return plans/reasons only. Missing history is a failure, rules filter normally."""
    now = utc(decision_at)
    if utc(evidence.available_at) > now:
        return Evaluation(rejected=(Rejection(ticker, None, "failed", ("source_not_available_at_decision",)),))
    try:
        stock, bench = prepare_history(ticker, rows, benchmark, calendar, now, config)
    except ValueError as exc:
        return Evaluation(rejected=(Rejection(ticker, None, "failed", (str(exc),)),))
    c = config
    close, high, low, opening, volume = (np.array([getattr(r, key) for r in stock], dtype=float)
                                       for key in ("close", "high", "low", "open", "volume"))
    fast, mid, slow = (ema(close, p) for p in (c.SWING_EMA_FAST, c.SWING_EMA_MID, c.SWING_EMA_SLOW))
    volatility = float(atr(high, low, close, c.SWING_ATR_PERIOD)[-1])
    bench_close = np.array([r.close for r in bench])
    rs = tuple((int(p), float(close[-1] / close[-int(p)-1] - bench_close[-1] / bench_close[-int(p)-1]))
               for p in c.SWING_RS_LOOKBACKS.split(","))
    lookback = c.SWING_VOLUME_LOOKBACK
    avg_volume = float(volume[-lookback-1:-1].mean())
    traded = float((volume[-lookback-1:-1] * close[-lookback-1:-1]).mean())
    rvol = float(volume[-1] / avg_volume)
    candle = high[-1] - low[-1]
    benchmark_mid = ema(bench_close, c.SWING_EMA_MID)[-1]
    shared = {"trend": close[-1] > fast[-1] > mid[-1] > slow[-1],
              "rising_trend": mid[-1] > mid[-c.SWING_SLOPE_SESSIONS-1],
              "market_regime": bench_close[-1] > benchmark_mid,
              "relative_strength": all(value >= 0 for _, value in rs),
              "liquidity": traded >= c.SWING_MIN_AVG_TRADED_VALUE_INR,
              "extension": volatility > 0 and close[-1] - fast[-1] <= c.SWING_MAX_EXTENSION_ATR * volatility,
              "candle": 0 < candle <= c.SWING_MAX_CANDLE_ATR * volatility and
                        (close[-1] - low[-1]) / candle >= c.SWING_MIN_CLOSE_LOCATION}
    recorded = []
    record_check(recorded, "trend", "Close / fast EMA / mid EMA", (close[-1], fast[-1], mid[-1]),
                 "all >", (fast[-1], mid[-1], slow[-1]), shared["trend"], units="INR",
                 note="Pairwise: close > fast EMA > mid EMA > slow EMA.")
    record_check(recorded, "rising_trend", "Mid EMA rising", mid[-1], ">", mid[-c.SWING_SLOPE_SESSIONS-1],
                 shared["rising_trend"], units="INR", note=f"Compared with {c.SWING_SLOPE_SESSIONS} completed sessions earlier.")
    record_check(recorded, "market_regime", "Benchmark above mid EMA", bench_close[-1], ">", benchmark_mid, shared["market_regime"], units="index points")
    record_check(recorded, "relative_strength", "Stock return minus benchmark return", tuple(value for _, value in rs),
                 "all >=", 0, shared["relative_strength"], units="return fraction", note=f"Lookback sessions, in order: {', '.join(str(p) for p, _ in rs)}.")
    record_check(recorded, "liquidity", "Prior average daily traded value", traded, ">=", c.SWING_MIN_AVG_TRADED_VALUE_INR,
                 shared["liquidity"], units="INR", note=f"Previous {lookback} sessions; signal bar excluded.")
    record_check(recorded, "extension", "Positive ATR / close minus fast EMA", (volatility, close[-1]-fast[-1]),
                 "> 0 and <=", (0, c.SWING_MAX_EXTENSION_ATR*volatility), shared["extension"], units="INR")
    location = (close[-1]-low[-1])/candle if candle > 0 else None
    record_check(recorded, "candle", "Range / range / close location", (candle, candle, location),
                 "> 0 and <= and >=", (0, c.SWING_MAX_CANDLE_ATR*volatility, c.SWING_MIN_CLOSE_LOCATION), shared["candle"],
                 note="Range in INR, then the close's fraction of the candle range. Zero range makes location unavailable.")
    if not all(shared.values()):
        return Evaluation(rejected=(Rejection(ticker, None, "filtered", tuple(k for k, v in shared.items() if not v)),), checks=tuple(recorded))
    recent = slice(-c.SWING_PULLBACK_LOOKBACK-1, -1)
    resistance = high[-c.SWING_BREAKOUT_LOOKBACK-1:-1].max()
    recent_rvol = [volume[i] / volume[i-lookback:i].mean()
                   for i in range(len(stock)-c.SWING_PULLBACK_LOOKBACK-1, len(stock)-1)]
    rules = {
        "DAILY_BREAKOUT_V1": {
            "breakout_close": close[-1] > resistance,
            "breakout_extension": close[-1] - resistance <= c.SWING_BREAKOUT_EXTENSION_ATR * volatility,
            "breakout_volume": rvol >= c.SWING_BREAKOUT_RVOL},
        "DAILY_PULLBACK_V1": {
            "pullback_touch": bool(np.any(low[recent] <= fast[recent] + c.SWING_PULLBACK_TOUCH_ATR * volatility)),
            "pullback_support": bool(np.all(close[recent] >= mid[recent])),
            "pullback_depth": high[recent].max() - low[recent].min() <= c.SWING_PULLBACK_DEPTH_ATR * volatility,
            "pullback_volume": float(np.mean(recent_rvol)) <= c.SWING_PULLBACK_MAX_RVOL,
            "reclaim_volume": rvol >= c.SWING_PULLBACK_RECLAIM_RVOL,
            "reclaim_close": close[-1] > high[-2] and close[-1] > opening[-1]}}
    breakout, pullback = "DAILY_BREAKOUT_V1", "DAILY_PULLBACK_V1"
    def capture(strategy, key, label, observed, operator, threshold, units="", note=""):
        record_check(recorded, key, label, observed, operator, threshold, rules[strategy][key], strategy=strategy, units=units, note=note)
    capture(breakout, "breakout_close", "Close above prior resistance", close[-1], ">", resistance, "INR",
            f"Maximum high of the previous {c.SWING_BREAKOUT_LOOKBACK} sessions; signal excluded.")
    capture(breakout, "breakout_extension", "Close minus resistance", close[-1]-resistance, "<=", c.SWING_BREAKOUT_EXTENSION_ATR*volatility, "INR")
    capture(breakout, "breakout_volume", "Signal relative volume", rvol, ">=", c.SWING_BREAKOUT_RVOL, "multiple", "Observed volume; no partial-session projection.")
    capture(pullback, "pullback_touch", "Prior lows touch fast EMA allowance", tuple(low[recent]), "any <=", tuple(fast[recent]+c.SWING_PULLBACK_TOUCH_ATR*volatility), "INR", "Pairwise by prior session; at least one must pass.")
    capture(pullback, "pullback_support", "Prior closes hold mid EMA", tuple(close[recent]), "all >=", tuple(mid[recent]), "INR", "Pairwise by prior session; every comparison must pass.")
    capture(pullback, "pullback_depth", "Prior peak-to-trough depth", high[recent].max()-low[recent].min(), "<=", c.SWING_PULLBACK_DEPTH_ATR*volatility, "INR")
    capture(pullback, "pullback_volume", "Prior mean relative volume", np.mean(recent_rvol), "<=", c.SWING_PULLBACK_MAX_RVOL, "multiple")
    capture(pullback, "reclaim_volume", "Reclaim relative volume", rvol, ">=", c.SWING_PULLBACK_RECLAIM_RVOL, "multiple")
    capture(pullback, "reclaim_close", "Close above previous high and signal open", (close[-1], close[-1]), "all >", (high[-2], opening[-1]), "INR")
    features = Features(float(close[-1]), volatility, float(fast[-1]), float(mid[-1]), float(slow[-1]),
                        rvol, avg_volume, traded, rs)
    missing = []
    for gate, valid in (("identity", evidence.identity_verified), ("publication", evidence.publication_verified),
                        ("trading_basis", evidence.trading_basis_verified and stock[-1].price_basis == "raw"),
                        ("sector", bool(evidence.sector) and evidence.sector_verified),
                        ("earnings", evidence.earnings_verified), ("surveillance", evidence.surveillance_verified)):
        if not valid:
            missing.append(gate)
    frozen_cfg = configuration(c)
    model = costs(c)
    config_hash = digest(frozen_cfg)
    data_hash = digest({"stock": [r.model_dump(mode="json") for r in stock],
                        "benchmark": [r.model_dump(mode="json") for r in bench], "evidence": asdict(evidence)})
    impl = implementation_hash()
    window = calendar.calendar.sessions[calendar.calendar.sessions > pd.Timestamp(stock[-1].session)][:c.SWING_ENTRY_VALID_SESSIONS]
    if len(window) < c.SWING_ENTRY_VALID_SESSIONS:
        return Evaluation(rejected=(Rejection(ticker, None, "failed", ("calendar_entry_window_unavailable",)),), checks=tuple(recorded))
    future = [s for s in window if calendar.calendar.session_open(s) > pd.Timestamp(now)]
    record_check(recorded, "entry_window_expired", "Eligible entry opens after the decision", len(future), ">", 0, bool(future), units="sessions", note="Existing signal expiry is never extended by refreshing the view.")
    if not future:
        return Evaluation(rejected=(Rejection(ticker, None, "filtered", ("entry_window_expired",)),), checks=tuple(recorded))
    plans, rejections = [], []
    for strategy, checks in rules.items():
        if not all(checks.values()):
            rejections.append(Rejection(ticker, strategy, "filtered", tuple(k for k, v in checks.items() if not v)))
            continue
        stop = float(min(low[-1], low[recent].min()) - c.SWING_STOP_BUFFER_ATR * volatility)
        distance = float(close[-1] - stop)
        stop_valid = not (stop <= 0 or not c.SWING_MIN_STOP_ATR * volatility <= distance <= c.SWING_MAX_STOP_ATR * volatility)
        record_check(recorded, "stop_distance", "Stop / stop distance / stop distance", (stop, distance, distance),
                     "> 0 and >= and <=", (0, c.SWING_MIN_STOP_ATR*volatility, c.SWING_MAX_STOP_ATR*volatility),
                     stop_valid,
                     strategy=strategy, units="INR", note="Positive stop and configured minimum/maximum ATR distance.")
        if not stop_valid:
            rejections.append(Rejection(ticker, strategy, "filtered", ("stop_distance",)))
            continue
        target = float(close[-1] + c.SWING_TARGET_R * distance)
        overhead = float(high[-c.SWING_OVERHEAD_LOOKBACK-1:-1].max())
        target_basis = "Mechanical configured R multiple; scenario, not forecast"
        if overhead > close[-1] + c.SWING_OVERHEAD_BUFFER_ATR * volatility and overhead < target:
            target, target_basis = overhead, "Capped at previous overhead high; scenario, not forecast"
        _, net_rr = payoff(float(close[-1]), stop, target, model)
        lower = float(max(close[-1] - c.SWING_ENTRY_DIP_ATR * volatility,
                          stop + c.SWING_ENTRY_STOP_CLEARANCE_ATR * volatility))
        upper = min(float(close[-1] + c.SWING_MAX_GAP_ATR * volatility),
                    model.sell_multiplier * (target + c.SWING_MIN_NET_RR * stop)
                    / (model.buy_multiplier * (1 + c.SWING_MIN_NET_RR)))
        reward_valid = not (net_rr < c.SWING_MIN_NET_RR or upper < lower)
        record_check(recorded, "net_reward_risk", "Net reward/risk / maximum entry", (net_rr, upper),
                     ">= and >=", (c.SWING_MIN_NET_RR, lower), reward_valid,
                     strategy=strategy, note="First compare net R multiple, then maximum versus minimum entry in INR. Nonfinite values are unavailable.")
        if not reward_valid:
            rejections.append(Rejection(ticker, strategy, "filtered", ("net_reward_risk",)))
            continue
        plan = Plan("", strategy, config_hash, impl, frozen_cfg, data_hash, evidence, ticker,
                    stock[-1].session, now, stock[-1].series, stock[-1].isin, stock[-1].price_basis,
                    features, lower, upper, future[0].date(), window[-1].date(),
                    stop, target, target_basis, (target - close[-1]) / distance, net_rr, model,
                    c.SWING_BREAKOUT_MAX_HOLD if strategy == "DAILY_BREAKOUT_V1" else c.SWING_PULLBACK_MAX_HOLD,
                    tuple(shared) + tuple(checks) + ("stop_distance", "net_reward_risk"), tuple(missing),
                    "Prior-high breakout with volume confirmation" if strategy == "DAILY_BREAKOUT_V1"
                    else "Controlled trend retracement followed by completed-bar reclaim",
                    requested_risk_inr=c.SWING_CAPITAL_INR * c.SWING_RISK_FRACTION if c.SWING_CAPITAL_INR is not None else None)
        plans.append(replace(plan, id=digest(asdict(plan))))
    return Evaluation(tuple(sorted(plans, key=priority)), tuple(rejections), tuple(recorded))

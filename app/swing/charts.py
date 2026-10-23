"""Recorded chart evidence outside immutable trade-plan and ledger schemas."""
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite

from app.swing.explanations import RuleCheck
from app.swing.models import DailyPrice
from app.swing.plans import Plan


@dataclass(frozen=True)
class ResistanceWindow:
    start: date
    end: date
    sessions: int
    level: float


@dataclass(frozen=True)
class VolumeBaseline:
    start: date
    end: date
    sessions: int
    average_volume: float
    signal_volume: float
    relative_volume: float
    minimum_relative_volume: float


@dataclass(frozen=True)
class AtrExtension:
    atr: float
    period: int
    distance: float
    multiple: float
    maximum_multiple: float
    maximum_close: float
    ema_fast_multiple: float
    ema_fast_maximum_multiple: float


@dataclass(frozen=True)
class BreakoutChartContext:
    plan_id: str
    ticker: str
    vintage: str
    price_basis: str
    session: date
    decision_at: datetime
    config_hash: str
    data_hash: str
    resistance: ResistanceWindow
    volume: VolumeBaseline
    extension: AtrExtension


def breakout_chart_context(plan: Plan, rows: tuple[DailyPrice, ...],
                           checks: tuple[RuleCheck, ...]) -> BreakoutChartContext | None:
    """Attach reached rule values and consumed-window dates to a breakout plan.

    This presents existing calculations; it never evaluates new setup rules or
    mutates a plan. Missing older rule metadata stays unavailable.
    """
    if plan.strategy != "DAILY_BREAKOUT_V1":
        return None
    cfg = dict(plan.configuration)
    recorded = {c.rule: c for c in checks if c.strategy == plan.strategy}
    if not {"breakout_close", "breakout_extension", "breakout_volume"} <= recorded.keys():
        return None
    prior = tuple(r for r in rows if r.session < plan.session)
    signal = tuple(r for r in rows if r.session == plan.session)
    n, v = int(cfg["SWING_BREAKOUT_LOOKBACK"]), int(cfg["SWING_VOLUME_LOOKBACK"])
    if len(prior) < max(n, v) or len(signal) != 1:
        return None
    used = (*prior[-max(n, v):], signal[0])
    dates = [r.session for r in used]
    if dates != sorted(set(dates)) or any(r.ticker != plan.ticker or r.price_basis != plan.price_basis for r in used):
        return None
    resistance = recorded["breakout_close"].threshold
    distance = recorded["breakout_extension"].observed
    ceiling_distance = recorded["breakout_extension"].threshold
    minimum_rvol = recorded["breakout_volume"].threshold
    f = plan.features
    values = (resistance, distance, ceiling_distance, minimum_rvol, f.close, f.ema_fast, f.atr, f.average_volume,
              f.volume_ratio, signal[0].volume, cfg["SWING_BREAKOUT_EXTENSION_ATR"], cfg["SWING_MAX_EXTENSION_ATR"])
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not isfinite(x) for x in values):
        return None
    if min(f.atr, f.average_volume, signal[0].volume) <= 0:
        return None
    return BreakoutChartContext(plan.id, plan.ticker, plan.evidence.vintage, plan.price_basis,
        plan.session, plan.decision_at, plan.config_hash, plan.data_hash,
        ResistanceWindow(prior[-n].session, prior[-1].session, n, float(resistance)),
        VolumeBaseline(prior[-v].session, prior[-1].session, v, f.average_volume,
                       float(signal[0].volume), f.volume_ratio, float(minimum_rvol)),
        AtrExtension(f.atr, int(cfg["SWING_ATR_PERIOD"]), float(distance), float(distance/f.atr),
                     float(cfg["SWING_BREAKOUT_EXTENSION_ATR"]), float(resistance+ceiling_distance),
                     float((f.close-f.ema_fast)/f.atr), float(cfg["SWING_MAX_EXTENSION_ATR"])))

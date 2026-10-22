"""Deterministic hypothetical allocations; no mutation or order submission."""
from dataclasses import dataclass
from datetime import date
import math

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import Settings
from app.swing.plans import Plan, priority
from app.swing.setups import configuration, costs, digest, payoff


class BookItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    ticker: str = Field(min_length=1)
    sector: str = Field(min_length=1)
    quantity: int = Field(gt=0, strict=True)
    mark: float = Field(gt=0)
    stop: float = Field(gt=0)
    kind: str = "held"
    session: date
    reserved_cash_per_share: float | None = Field(default=None, gt=0)
    reserved_risk_per_share: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_item(self):
        if self.kind not in {"held", "pending"} or not self.sector.strip():
            raise ValueError("Known sector and held/pending kind required")
        if self.kind == "pending" and self.stop >= self.mark:
            raise ValueError("Pending long stop must be below entry reservation")
        if self.kind == "pending" and (self.reserved_cash_per_share is None or
                self.reserved_risk_per_share is None or self.reserved_cash_per_share < self.mark):
            raise ValueError("Pending reservations need frozen cash and risk including costs")
        if self.kind == "pending" and self.reserved_risk_per_share < self.reserved_cash_per_share - self.stop:
            raise ValueError("Reserved risk understates even the pre-exit-fee stop loss")
        return self


class Account(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    nav: float = Field(gt=0)
    cash: float = Field(ge=0)  # settled cash BEFORE pending reservations
    session: date
    items: tuple[BookItem, ...] = ()

    @model_validator(mode="after")
    def reconciled(self):
        if len({x.ticker for x in self.items}) != len(self.items):
            raise ValueError("Aggregate each held/pending security before allocating")
        if any(x.session != self.session for x in self.items):
            raise ValueError("Stale or differently dated book")
        held = sum(x.mark * x.quantity for x in self.items if x.kind == "held")
        if not math.isclose(self.cash + held, self.nav, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError("Cash plus marked held equities must reconcile to NAV")
        return self


@dataclass(frozen=True)
class ReturnPoint:
    session: date
    value: float


@dataclass(frozen=True)
class Allocation:
    plan_id: str
    ticker: str
    quantity: int | None
    entry: float
    cash_required: float | None
    estimated_entry_cost: float | None
    planned_stop_risk: float | None
    net_rr: float | None
    limiting_constraints: tuple[str, ...]
    reasons: tuple[str, ...]
    # Allocation is only a what-if calculation, even if the quantity is positive.
    status: str = "HYPOTHETICAL_ONLY"
    portfolio_gross_after: float | None = None
    portfolio_open_risk_after: float | None = None
    sector_value_after: float | None = None


def correlation_ok(left, right, expected_sessions, config: Settings) -> tuple[bool, str | None]:
    """No gap compression, date filling or correlation from a single observation."""
    expected = tuple(expected_sessions)[-config.SWING_CORRELATION_SESSIONS:]
    if len(expected) < config.SWING_MIN_CORRELATION_OBSERVATIONS:
        return False, "correlation_history_missing"
    arrays = []
    for data in (left, right):
        if data is None:
            return False, "correlation_history_missing"
        observed = [point.session for point in data]
        if observed != sorted(set(observed)):
            return False, "correlation_dates_invalid"
        mapping = {p.session: p.value for p in data}
        if any(d not in mapping for d in expected):
            return False, "correlation_history_missing"
        a = np.array([mapping[d] for d in expected], dtype=float)
        if not np.isfinite(a).all() or np.any(a <= -1) or np.std(a) == 0:
            return False, "correlation_values_invalid"
        arrays.append(a)
    value = float(np.corrcoef(*arrays)[0, 1])
    if not math.isfinite(value):
        return False, "correlation_values_invalid"
    return (True, None) if abs(value) <= config.SWING_MAX_CORRELATION else (False, "correlation_limit")


def allocate(plans: tuple[Plan, ...], config: Settings, account: Account | None,
             returns: dict[str, tuple[ReturnPoint, ...]], expected_return_sessions: tuple[date, ...],
             observed_opens: dict[str, float] | None = None, *, calendar=None,
             dynamic_nav: bool = False) -> tuple[Allocation, ...]:
    """Preview one deterministic batch against held AND pending allocations.

    Observed opens are an internal S3/replay seam, never an HTTP override. Without
    them use the worst entry in the frozen interval. Reservations aren't written.
    """
    c = config
    output = []
    ordered = sorted(plans, key=priority)
    model = costs(c)
    held = list(account.items) if account else []
    cash = account.cash if account else 0
    if account:
        cash -= sum(x.reserved_cash_per_share * x.quantity for x in held if x.kind == "pending")
    for plan in ordered:
        price = (observed_opens or {}).get(plan.ticker, plan.entry_max)
        reasons = []
        risk, rr = payoff(price, plan.stop, plan.target, plan.costs) if math.isfinite(price) and price > 0 else (0, None)
        if plan.costs != model:
            reasons.append("cost_configuration_changed")
        frozen, configured = dict(plan.configuration), dict(configuration(c))
        if dynamic_nav:
            # S3's reconciled account NAV is a runtime valuation, not strategy tuning.
            frozen.pop("SWING_CAPITAL_INR", None)
            configured.pop("SWING_CAPITAL_INR", None)
        if (digest(frozen) != digest(configured) if dynamic_nav else plan.config_hash != digest(configuration(c))):
            reasons.append("plan_configuration_changed")
        if not math.isfinite(price) or not plan.entry_min <= price <= plan.entry_max or price <= plan.stop:
            reasons.append("entry_gap_outside_interval")
        if rr is None or (rr < c.SWING_MIN_NET_RR and not math.isclose(rr, c.SWING_MIN_NET_RR, rel_tol=1e-12)):
            reasons.append("net_reward_risk")
        reasons.extend(plan.missing_gates)
        if c.SWING_CAPITAL_INR is None and not dynamic_nav:
            reasons.append("capital_not_configured")
        if account is None:
            reasons.append("reconciled_account_required")
        elif account.session != plan.session and not dynamic_nav:
            reasons.append("stale_account")
        elif not dynamic_nav and not math.isclose(account.nav, c.SWING_CAPITAL_INR or account.nav, rel_tol=1e-9):
            reasons.append("capital_nav_mismatch")
        if any(x.ticker == plan.ticker for x in held):
            reasons.append("already_held_or_reserved")
        if account and held:
            if calendar is None:
                reasons.append("correlation_calendar_required")
            elif (not expected_return_sessions or expected_return_sessions[-1] != plan.session
                    or tuple(expected_return_sessions) != tuple(sorted(set(expected_return_sessions)))
                    or tuple(expected_return_sessions) != tuple(s.date() for s in calendar.calendar.sessions_in_range(
                        expected_return_sessions[0], plan.session))):
                reasons.append("correlation_sessions_invalid")
            else:
                for item in held:
                    okay, why = correlation_ok(returns.get(plan.ticker), returns.get(item.ticker),
                                              expected_return_sessions, c)
                    if not okay:
                        reasons.append(why)
        if reasons:
            output.append(Allocation(plan.id, plan.ticker, None if c.SWING_CAPITAL_INR is None or account is None else 0,
                                     price if math.isfinite(price) else plan.entry_max, None, None, None,
                                     rr if rr is not None and math.isfinite(rr) else None, (), tuple(dict.fromkeys(reasons))))
            continue
        gross = sum(x.mark * x.quantity for x in held)
        heat = sum((x.reserved_risk_per_share if x.kind == "pending" else
                    max(0, x.mark - x.stop * model.sell_multiplier, x.reserved_risk_per_share or 0)) * x.quantity for x in held)
        sector = sum(x.mark * x.quantity for x in held if x.sector == plan.evidence.sector)
        unit_cash = price * model.buy_multiplier
        limits = {"cash": max(0, cash) / unit_cash,
                  "position": account.nav * c.SWING_MAX_POSITION_FRACTION / price,
                  "gross": max(0, account.nav * c.SWING_MAX_GROSS_FRACTION - gross) / price,
                  "trade_risk": account.nav * c.SWING_RISK_FRACTION / risk,
                  "open_risk": max(0, account.nav * c.SWING_MAX_OPEN_RISK_FRACTION - heat) / risk,
                  "sector": max(0, account.nav * c.SWING_MAX_SECTOR_FRACTION - sector) / price,
                  "liquidity": plan.features.average_volume * c.SWING_MAX_ADV_PARTICIPATION}
        minimum = min(limits.values())
        qty = math.floor(minimum)
        binding = tuple(key for key, value in limits.items() if math.floor(value) == qty)
        output.append(Allocation(plan.id, plan.ticker, qty, price, qty * unit_cash,
                                 qty * (unit_cash - price), qty * risk, rr, binding,
                                 () if qty else ("zero_share_allocation",),
                                 portfolio_gross_after=gross + qty * price,
                                 portfolio_open_risk_after=heat + qty * risk,
                                 sector_value_after=sector + qty * price))
        if qty:
            cash -= qty * unit_cash
            held.append(BookItem(ticker=plan.ticker, sector=plan.evidence.sector, quantity=qty,
                                 mark=price, stop=plan.stop, kind="pending", session=plan.session,
                                 reserved_cash_per_share=unit_cash, reserved_risk_per_share=risk))
    return tuple(output)

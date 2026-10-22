"""Conservative daily-OHLC research lifecycle; never a broker or promoted model."""
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal
import math

import pandas as pd

from app.core.config import Settings
from app.providers.swing_archive import last_completed
from app.swing.ledger import PaperConflict, money
from app.swing.models import utc
from app.swing.plans import priority
from app.swing.risk import Account, BookItem, allocate
from app.swing.setups import payoff


class ReplayEngine:
    def __init__(self, ledger, market, calendar, config, clock):
        self.ledger, self.market, self.calendar, self.config, self.clock = ledger, market, calendar, config, clock

    def entry_reference(self, price, plan=None):
        """Observed open in S3; S4 may override execution impact in an isolated book."""
        return price

    def exit_reference(self, price, position=None):
        return price

    def _problem(self, obs, ticker, session, isin=None):
        bar = obs.bar
        if obs.available_at > utc(self.clock.now()):
            return "observation_not_available"
        if bar is None:
            return obs.reason or "missing_bar"
        if bar.ticker != ticker or bar.session != session or bar.price_basis != "raw":
            return "execution_identity_session_or_basis_mismatch"
        if bar.instrument != ("index" if ticker == self.config.SWING_BENCHMARK else "equity"):
            return "execution_instrument_mismatch"
        if isin and bar.isin != isin and not (obs.split_ratio and obs.previous_isin == isin):
            return "security_identity_mismatch"
        if not obs.actions_verified or obs.unsupported_action:
            return "corporate_action_context_unresolved"
        return None

    @staticmethod
    def _unresolved(emit, session, ticker, reason, plan_id=None):
        emit("DATA_UNRESOLVED", {"session": str(session), "ticker": ticker,
                                 "plan_id": plan_id, "reason": reason})

    def _book(self, state, session, overrides=None):
        items = []
        for pos in state["positions"].values():
            mark = (overrides or {}).get(pos["ticker"], pos["mark"])
            items.append(BookItem(ticker=pos["ticker"], sector=pos["sector"], quantity=pos["quantity"],
                                  mark=mark, stop=pos["stop"], session=session,
                                  reserved_risk_per_share=max(0, mark-pos["stop"]*pos["sell_multiplier"]) or None))
        for order in state["orders"].values():
            if order["lifecycle"] in {"ARMED", "UNRESOLVED"}:
                items.append(BookItem(ticker=order["ticker"], sector=order["sector"], quantity=order["quantity"],
                                      mark=order["entry_max"], stop=order["stop"], kind="pending", session=session,
                                      reserved_cash_per_share=float(money(order["reserved_cash"])/order["quantity"]),
                                      reserved_risk_per_share=order["risk_per_share"]))
        held = sum(x.mark*x.quantity for x in items if x.kind == "held")
        return Account(nav=float(state["cash"])+held, cash=float(state["cash"]), session=session, items=tuple(items))

    def _allocation(self, conn, state, plan, session, price=None, marks=None, omit_order=None):
        if any(p["uncertain"] for p in state["positions"].values()) or any(
                o["lifecycle"] == "UNRESOLVED" for o in state["orders"].values()):
            return None, ("unresolved_account_liabilities",)
        if any(p["mark_session"] != str(date.fromisoformat(state["last_session"])) for p in state["positions"].values()) and marks is None:
            return None, ("stale_account_marks",)
        # Runtime book/NAV is distinct from the immutable strategy/cost parameters.
        cfg = Settings(_env_file=None, **{**self.config.model_dump(), **dict(plan.configuration)})
        temporary = state
        if omit_order:
            temporary = {**state, "orders": {k: v for k, v in state["orders"].items() if k != omit_order}}
        account = self._book(temporary, session, marks)
        days = tuple(x.date() for x in self.calendar.calendar.sessions if x.date() <= plan.session)[-cfg.SWING_CORRELATION_SESSIONS:]
        tickers = {plan.ticker, *(i.ticker for i in account.items)}
        returns = {ticker: self.market.returns(ticker, plan.session, cfg.SWING_CORRELATION_SESSIONS,
                                              plan.evidence.vintage, plan.decision_at) for ticker in tickers} if account.items else {}
        result = allocate((plan,), cfg, account, returns, days,
                          {plan.ticker: price} if price is not None else None,
                          calendar=self.calendar, dynamic_nav=True)[0]
        return result, result.reasons

    def _reserve(self, conn, state, emit, session):
        plans = [self.ledger.plan(state["account_id"], key, conn) for key, order in state["orders"].items()
                 if order["lifecycle"] == "CANDIDATE"]
        for plan in sorted(plans, key=priority):
            if plan.session > session:
                continue
            if plan.session < session or self.calendar.calendar.session_open(str(plan.first_entry_session)) <= pd.Timestamp(self.clock.now()):
                emit("ORDER_EXPIRED", {"plan_id": plan.id, "reason": "entry_window_not_captured_in_time", "session": str(session)})
                continue
            result, reasons = self._allocation(conn, state, plan, session)
            if reasons or not result or not result.quantity:
                emit("ORDER_CANCELLED", {"plan_id": plan.id, "session": str(session), "reason": list(reasons or ("zero_share_allocation",))})
                continue
            reserve = money(Decimal(str(plan.entry_max))*Decimal(str(plan.costs.buy_multiplier))*result.quantity, reserve=True)
            emit("ORDER_RESERVED", {"plan_id": plan.id, "ticker": plan.ticker, "quantity": result.quantity,
                                    "reserved_cash": str(reserve), "risk_per_share": max(
                                        payoff(plan.entry_max, plan.stop, plan.target, plan.costs)[0],
                                        float(reserve/result.quantity)-plan.stop*plan.costs.sell_multiplier),
                                    "entry_min": plan.entry_min, "entry_max": plan.entry_max, "stop": plan.stop,
                                    "target": plan.target, "sector": plan.evidence.sector,
                                    "basis_ratio": "1", "session": str(session)})

    def _split(self, state, emit, pos, obs, session):
        if obs.split_ratio is None:
            return True
        action_key = f"{pos['plan_id']}:{session}:{obs.action_id}"
        if action_key in state["actions"]:
            return True
        ratio = Decimal(obs.split_ratio)
        quantity = Decimal(pos["quantity"])*ratio
        if not ratio.is_finite() or ratio <= 0 or quantity != quantity.to_integral_value():
            self._unresolved(emit, session, pos["ticker"], "fractional_or_invalid_action_requires_reconciliation", pos["plan_id"])
            return False
        divisor = float(ratio)
        changes = {"quantity": int(quantity), "mark": pos["mark"]/divisor,
                   "stop": pos["stop"]/divisor, "target": pos["target"]/divisor,
                   "entry_price": pos["entry_price"]/divisor, "isin": obs.bar.isin}
        emit("SPLIT_APPLIED", {"ticker": pos["ticker"], "session": str(session), "action_key": action_key,
                               "previous_quantity": pos["quantity"], "quantity_delta": int(quantity)-pos["quantity"],
                               "position_changes": changes, "evidence": obs.evidence})
        return True

    def _exit(self, state, emit, pos, obs, session, *, open_only):
        if pos.get("action_unresolved"):
            return True
        problem = self._problem(obs, pos["ticker"], session, pos["isin"])
        if problem:
            if open_only:
                self._unresolved(emit, session, pos["ticker"], problem, pos["plan_id"])
            return True
        bar = obs.bar
        reason, reference, ambiguous = None, None, False
        if pos.get("exit_pending"):
            reason, reference = pos["exit_pending"], bar.open
        elif bar.open <= pos["stop"]:
            reason, reference = "STOP_GAP", bar.open
        elif bar.open >= pos["target"]:
            reason, reference = "TARGET_GAP", pos["target"]
        elif not open_only:
            if bar.low <= pos["stop"]:
                reason, reference = "STOP", pos["stop"]
                if bar.high >= pos["target"]:
                    ambiguous = True
            elif bar.high >= pos["target"]:
                reason, reference = "TARGET", pos["target"]
        if not reason:
            return False
        emit("EXIT_PENDING", {"ticker": pos["ticker"], "reason": reason, "session": str(session)})
        if obs.tradable is None or obs.sell_capacity is None:
            self._unresolved(emit, session, pos["ticker"], "exit_tradability_or_capacity_unknown", pos["plan_id"])
            return True
        quantity = min(pos["quantity"], obs.sell_capacity) if obs.tradable else 0
        if quantity:
            if ambiguous:
                sensitivity = money(Decimal(str(pos["target"]-pos["stop"]))*Decimal(str(pos["sell_multiplier"]))*quantity)
                emit("AMBIGUOUS_EXIT", {"ticker": pos["ticker"], "plan_id": pos["plan_id"], "session": str(session),
                                        "policy": "STOP_FIRST", "target_first_incremental_proceeds": str(sensitivity),
                                        "quantity": quantity, "evidence": obs.evidence})
            execution_reference = self.exit_reference(reference, pos)
            value = money(Decimal(str(execution_reference))*Decimal(str(pos["sell_multiplier"]))*quantity)
            emit("SELL_FILLED", {"ticker": pos["ticker"], "plan_id": pos["plan_id"], "quantity": quantity,
                                  "raw_price": reference, "execution_reference": execution_reference, "multiplier": pos["sell_multiplier"], "cash_value": str(value),
                                  "gross_value": str(money(Decimal(str(reference))*quantity)), "session": str(session),
                                  "reason": reason, "approximation": "DAILY_OHLC", "evidence": obs.evidence})
        return True

    def _entries(self, conn, state, emit, observations, benchmark, session):
        open_marks = {ticker: obs.bar.open for ticker, obs in observations.items()
                      if ticker in state["positions"] and not self._problem(obs, ticker, session, state["positions"][ticker]["isin"])}
        for key, order in list(state["orders"].items()):
            if order["lifecycle"] != "ARMED":
                continue
            plan = self.ledger.plan(state["account_id"], key, conn)
            if session < plan.first_entry_session:
                continue
            if session > plan.expiry_session:
                emit("ORDER_EXPIRED", {"plan_id": key, "reason": "entry_window_expired", "session": str(session)})
                continue
            obs = observations[plan.ticker]
            problem = self._problem(obs, plan.ticker, session, plan.isin)
            if obs.split_ratio:
                # Pending orders need a freshly reviewed trading-basis plan rather
                # than guessing executable ticks/entitlements from an old interval.
                problem = "pending_order_corporate_action_requires_reconciliation"
            if problem or self._problem(benchmark, self.config.SWING_BENCHMARK, session) or benchmark.bar.open is None:
                self._unresolved(emit, session, plan.ticker, problem or "benchmark_unavailable", key)
                continue
            bar = obs.bar
            reference = self.entry_reference(bar.open, plan)
            if not plan.entry_min <= reference <= plan.entry_max or obs.tradable is False:
                emit("ORDER_EXPIRED", {"plan_id": key, "reason": "entry_gap_or_not_tradable", "session": str(session)})
                continue
            if obs.tradable is None or obs.buy_capacity is None or set(open_marks) != set(state["positions"]):
                self._unresolved(emit, session, plan.ticker, "entry_capacity_tradability_or_held_marks_unknown", key)
                continue
            allocation, reasons = self._allocation(conn, state, plan, session, reference, open_marks, key)
            quantity = min(order["quantity"], obs.buy_capacity, allocation.quantity or 0) if allocation else 0
            if reasons:
                emit("ORDER_CANCELLED", {"plan_id": key, "reason": list(reasons), "session": str(session)})
                continue
            if not quantity:
                emit("ORDER_EXPIRED", {"plan_id": key, "reason": "zero_open_capacity_or_allocation", "session": str(session)})
                continue
            cash_value = money(Decimal(str(reference))*Decimal(str(plan.costs.buy_multiplier))*quantity)
            if cash_value > state["cash"]:
                raise PaperConflict("Fill exceeds reconciled cash")
            position = {"ticker": plan.ticker, "plan_id": key, "quantity": quantity, "initial_quantity": quantity,
                        "entry_price": reference, "entry_cash": str(cash_value), "proceeds": "0.00",
                        "stop": plan.stop, "target": plan.target, "mark": bar.open, "mark_session": str(session),
                        "entry_session": str(session), "sector": plan.evidence.sector, "isin": plan.isin,
                        "sell_multiplier": plan.costs.sell_multiplier,
                        "initial_risk": str(money(payoff(reference, plan.stop, plan.target, plan.costs)[0]*quantity)),
                        "max_hold": plan.max_hold_sessions, "uncertain": False, "exit_pending": None}
            emit("BUY_FILLED", {"plan_id": key, "ticker": plan.ticker, "quantity": quantity,
                                 "raw_price": bar.open, "execution_reference": reference, "multiplier": plan.costs.buy_multiplier,
                                 "gross_value": str(money(Decimal(str(bar.open))*quantity)), "cash_value": str(cash_value),
                                 "cancelled_remainder": order["quantity"]-quantity, "position": position,
                                 "session": str(session), "approximation": "VERIFIED_OPEN_CAPACITY", "evidence": obs.evidence})
            open_marks[plan.ticker] = bar.open

    def advance(self, conn, state, emit, vintage, through, *, allow_new_entries=True):
        now = utc(self.clock.now())
        if through > date.fromisoformat(last_completed(self.calendar, now)) or not self.calendar.calendar.is_session(str(through)):
            raise ValueError("Replay end must be a completed exchange session")
        previous = date.fromisoformat(state["last_session"])
        sessions = [s.date() for s in self.calendar.calendar.sessions_in_range(previous, through) if s.date() > previous] if through >= previous else []
        if through < previous or len(sessions) > self.config.SWING_REPLAY_MAX_SESSIONS:
            raise ValueError("Replay range moves backward or exceeds the configured bound")
        # Reserve at the already marked seed/last close before its next open.
        if allow_new_entries:
            self._reserve(conn, state, emit, previous)
        else:
            for key, order in list(state["orders"].items()):
                if order["lifecycle"] in {"CANDIDATE", "ARMED"}:
                    emit("ORDER_CANCELLED", {"plan_id": key, "ticker": order["ticker"], "session": str(previous),
                                             "reason": "Explicit management-only replay suppresses unfilled new entries"})
        for session in sessions:
            active = {**{t: p for t, p in state["positions"].items()},
                      **{o["ticker"]: o for o in state["orders"].values() if o["lifecycle"] == "ARMED"}}
            observations = {ticker: self.market.observe(ticker, session, vintage, now) for ticker in active}
            benchmark = self.market.observe(self.config.SWING_BENCHMARK, session, vintage, now)
            open_exits = set()
            for ticker, pos in list(state["positions"].items()):
                obs = observations[ticker]
                problem = self._problem(obs, ticker, session, pos["isin"])
                if not problem and not self._split(state, emit, pos, obs, session):
                    open_exits.add(ticker)
                    continue
                if self._exit(state, emit, pos, obs, session, open_only=True):
                    open_exits.add(ticker)
            if allow_new_entries:
                self._entries(conn, state, emit, observations, benchmark, session)
            for ticker, pos in list(state["positions"].items()):
                if ticker not in open_exits:
                    self._exit(state, emit, pos, observations[ticker], session, open_only=False)
            marks, stale = {}, []
            for ticker, pos in state["positions"].items():
                obs = observations[ticker]
                if pos.get("action_unresolved") or self._problem(obs, ticker, session, pos["isin"]):
                    stale.append(ticker)
                else:
                    marks[ticker] = obs.bar.close
                held = len(self.calendar.calendar.sessions_in_range(pos["entry_session"], session))
                if held >= pos["max_hold"] and not pos["exit_pending"]:
                    emit("EXIT_PENDING", {"ticker": ticker, "reason": "TIME_EXIT", "session": str(session)})
            benchmark_problem = self._problem(benchmark, self.config.SWING_BENCHMARK, session)
            benchmark_base = state["benchmark_base"]
            benchmark_value = None
            if not benchmark_problem and benchmark.bar.open is None:
                benchmark_problem = "benchmark_open_unavailable"
            if not benchmark_problem:
                if benchmark_base is None and not state["nav"]:
                    benchmark_base = benchmark.bar.open
                if benchmark_base is not None:
                    benchmark_value = str(money(state["initial_cash"]*Decimal(str(benchmark.bar.close))/Decimal(str(benchmark_base))))
                else:
                    benchmark_problem = "benchmark_initial_open_unavailable"
            unresolved_orders = [o["ticker"] for o in state["orders"].values() if o["lifecycle"] == "UNRESOLVED"]
            nav = None if stale or unresolved_orders else str(money(state["cash"] + sum((Decimal(str(marks[t]))*p["quantity"]
                                           for t, p in state["positions"].items()), Decimal(0))))
            emit("SESSION_MARKED", {"session": str(session), "marks": marks, "stale_marks": stale,
                                     "cash": str(state["cash"]), "nav": nav, "benchmark_nav": benchmark_value,
                                     "unresolved_orders": unresolved_orders,
                                     "benchmark_base": benchmark_base, "benchmark_status": benchmark_problem,
                                     "observations": {ticker: {"bar": obs.bar.model_dump(mode="json") if obs.bar else None,
                                         "available_at": str(obs.available_at), "evidence": obs.evidence,
                                         "actions_verified": obs.actions_verified, "tradable": obs.tradable,
                                         "buy_capacity": obs.buy_capacity, "sell_capacity": obs.sell_capacity,
                                         "action_id": obs.action_id, "split_ratio": obs.split_ratio,
                                         "previous_isin": obs.previous_isin, "unsupported_action": obs.unsupported_action}
                                         for ticker, obs in {**observations, self.config.SWING_BENCHMARK: benchmark}.items()},
                                     "vintage": vintage, "quality": "UNRESOLVED" if state["unresolved"] or benchmark_problem or stale else "OBSERVED"})
            if allow_new_entries:
                self._reserve(conn, state, emit, session)
        emit("REPLAY_RECORDED", {"through": str(through), "vintage": vintage, "sessions": len(sessions),
                                  "policy": "S3_RESEARCH_REPLAY_V1"})
        return {"sessions_processed": len(sessions), "through": str(through),
                "open_positions": len(state["positions"]), "unresolved": len(state["unresolved"])}

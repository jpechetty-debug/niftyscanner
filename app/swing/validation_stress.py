"""Isolated counterfactual S3 replay. Observed bars and production stores stay intact."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
import json

from app.swing.execution import ExecutionObservation
from app.swing.ledger import empty_state, encoded, reduce_event
from app.swing.models import DailyPrice, utc
from app.swing.replay import ReplayEngine
from app.swing.setups import digest


class RecordedClock:
    """Ex-post timeline from authenticated source events, never historical publication proof."""
    def __init__(self, instant):
        self.instant = utc(instant)

    def now(self):
        return self.instant


class MemoryLedger:
    def __init__(self, source, clock, policy):
        self.account_id, self.clock, self.policy = source["account_id"], clock, policy
        self.plans = source["_plans"]
        self.rows = []
        self.state = empty_state(self.account_id)

    def emit(self, kind, payload):
        material = {"account_id": self.account_id, "seq": len(self.rows)+1, "command_key": "S4_COUNTERFACTUAL",
            "kind": kind, "created_at": self.clock.now().isoformat(),
            "payload": encoded({**payload, "counterfactual_policy_sha256": digest(self.policy)}),
            "prev_hash": self.rows[-1]["event_hash"] if self.rows else ""}
        reduce_event(self.state, kind, json.loads(material["payload"]))
        self.state["version"] = material["seq"]
        self.rows.append({**material, "event_hash": digest(material)})

    def plan(self, account_id, plan_id, conn=None):
        return self.plans[plan_id]

    def _state(self, conn, account_id):
        # Reconstruct/check the derived chain, like S3, before extracting metrics.
        rebuilt, previous = empty_state(account_id), ""
        for event in self.rows:
            material = {k: event[k] for k in ("account_id", "seq", "command_key", "kind", "created_at", "payload", "prev_hash")}
            if event["prev_hash"] != previous or digest(material) != event["event_hash"]:
                raise ValueError("Counterfactual event chain mismatch")
            reduce_event(rebuilt, event["kind"], json.loads(event["payload"]))
            rebuilt["version"], previous = event["seq"], event["event_hash"]
        return rebuilt

    def execute(self, query, parameters):
        if query.startswith("SELECT * FROM events"):
            return self.rows
        if query.startswith("SELECT id FROM plans"):
            return [(key,) for key in self.plans]
        raise ValueError("Unsupported in-memory validation query")


class RecordedMarket:
    def __init__(self, source, fallback):
        self.fallback, self.observations = fallback, {}
        for event in source["_events"]:
            if event["kind"] != "SESSION_MARKED":
                continue
            payload = event["payload"]
            for ticker, record in payload["observations"].items():
                self.observations[ticker, payload["session"]] = ExecutionObservation(
                    DailyPrice.model_validate(record["bar"]) if record["bar"] else None,
                    datetime.fromisoformat(record["available_at"]), record["evidence"],
                    actions_verified=record["actions_verified"], tradable=record["tradable"],
                    buy_capacity=record["buy_capacity"], sell_capacity=record["sell_capacity"],
                    action_id=record["action_id"], split_ratio=record["split_ratio"],
                    previous_isin=record["previous_isin"], unsupported_action=record["unsupported_action"])

    def observe(self, ticker, session, vintage, as_of):
        return self.observations.get((ticker, str(session))) or self.fallback.observe(ticker, session, vintage, as_of)

    def returns(self, *args):
        return self.fallback.returns(*args)


class AdverseReplay(ReplayEngine):
    def __init__(self, *args, policy):
        super().__init__(*args)
        self.policy, self.halts, self.affected = policy, {}, set()

    def selected(self, plan_id):
        score = int(digest((self.policy["seed"], self.policy["scenario"], plan_id)), 16)/2**256
        return score < self.policy["fraction"]

    def entry_reference(self, price, plan=None):
        if self.policy["scenario"] == "gap" and self.selected(plan.id):
            self.affected.add(plan.id)
            return price*(1+self.policy["gap_bps"]/10000)
        return price

    def exit_reference(self, price, position=None):
        if self.policy["scenario"] == "gap" and self.selected(position["plan_id"]):
            self.affected.add(position["plan_id"])
            return price*(1-self.policy["gap_bps"]/10000)
        return price

    def _entries(self, conn, state, emit, observations, benchmark, session):
        modified = dict(observations)
        if self.policy["scenario"] == "delay":
            for key, order in state["orders"].items():
                plan = self.ledger.plan(state["account_id"], key)
                age = len(self.calendar.calendar.sessions_in_range(plan.first_entry_session, session)) if session >= plan.first_entry_session else 0
                if order["lifecycle"] == "ARMED" and 0 < age <= self.policy["delay_sessions"] and self.selected(key):
                    self.affected.add(key)
                    # Counterfactual no opening execution, not a fabricated late price/fill.
                    modified[plan.ticker] = replace(modified[plan.ticker], buy_capacity=0)
        return super()._entries(conn, state, emit, modified, benchmark, session)

    def _exit(self, state, emit, pos, obs, session, *, open_only):
        bar, key = obs.bar, pos["plan_id"]
        if self.policy["scenario"] == "halt" and self.selected(key) and bar and not self._problem(obs, pos["ticker"], session, pos["isin"]):
            triggered = (pos["exit_pending"] or bar.open <= pos["stop"] or bar.open >= pos["target"]
                or (not open_only and (bar.low <= pos["stop"] or bar.high >= pos["target"])))
            if triggered and key not in self.halts:
                release = session
                for _ in range(self.policy["halt_sessions"]):
                    release = self.calendar.calendar.next_session(str(release)).date()
                self.halts[key] = release
                self.affected.add(key)
            if key in self.halts and session < self.halts[key]:
                obs = replace(obs, sell_capacity=0)
        return super()._exit(state, emit, pos, obs, session, open_only=open_only)


def stress_replay(source, scenario, gates, config, calendar, fallback, variant, window, window_schedule, study, frozen, now):
    """Replay all frozen captured opportunities; risk/cash/IOC policies are still S3."""
    from app.swing.validation import extract_panel
    policy = {"scenario": scenario, "seed": gates.seed, "fraction": gates.execution_stress_fraction,
              "delay_sessions": gates.entry_delay_sessions, "gap_bps": gates.adverse_gap_bps,
              "halt_sessions": gates.exit_halt_sessions, "version": "S4_ADVERSE_EXECUTION_V1"}
    events = source["_events"]
    clock = RecordedClock(datetime.fromisoformat(events[0]["created_at"]))
    ledger = MemoryLedger(source, clock, policy)
    engine = AdverseReplay(ledger, RecordedMarket(source, fallback), calendar, config, clock, policy=policy)
    for event in events:
        clock.instant = utc(datetime.fromisoformat(event["created_at"]))
        kind, payload = event["kind"], event["payload"]
        if kind in {"ACCOUNT_FUNDED", "PLAN_CAPTURED"}:
            ledger.emit(kind, deepcopy(payload))
        elif kind in {"ORDER_RESERVED", "ORDER_CANCELLED", "ORDER_EXPIRED"}:
            engine._reserve(ledger, ledger.state, ledger.emit, date.fromisoformat(payload["session"]))
        elif kind == "SESSION_MARKED":
            engine.advance(ledger, ledger.state, ledger.emit, payload["vintage"], date.fromisoformat(payload["session"]))
    panel = extract_panel(ledger, ledger, source["account_id"], variant, window, window_schedule, study, frozen, calendar, now)
    if not engine.affected:
        panel["failures"].append("no_opportunities_affected_by_prespecified_stress")
    return {"policy": policy, "policy_sha256": digest(policy), "affected_plans": sorted(engine.affected),
            "source_event_head": source["manifest"]["event_head"], "derived_event_head": panel["manifest"]["event_head"],
            "panel": panel, "scope": "Ex-post counterfactual portfolio; observed bars unchanged, no production events written"}

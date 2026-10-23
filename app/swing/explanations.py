"""Read-response explanations; never part of a frozen trade plan or ledger."""
from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Literal


Value = float | str | bool | None
Operand = Value | tuple[Value, ...]


@dataclass(frozen=True)
class RuleCheck:
    rule: str
    label: str
    observed: Operand
    operator: str
    threshold: Operand
    passed: bool | None
    strategy: str | None = None
    units: str = ""
    note: str = ""


@dataclass(frozen=True)
class RuleExplanation:
    ticker: str
    vintage: str
    decision_at: datetime
    data_session: date
    outcome: Literal["candidate", "filtered", "failed"]
    checks: tuple[RuleCheck, ...] = ()
    reasons: tuple[str, ...] = ()


def _value(value) -> Operand:
    if isinstance(value, (tuple, list)):
        return tuple(_value(item) for item in value)
    if value is None or isinstance(value, (str, bool)):
        return value
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def record_check(checks: list[RuleCheck], rule: str, label: str, observed,
                 operator: str, threshold, passed, *, strategy: str | None = None,
                 units: str = "", note: str = "") -> None:
    """Capture existing operands/outcomes without evaluating a second rule formula."""
    checks.append(RuleCheck(rule, label, _value(observed), operator, _value(threshold),
                            bool(passed) if passed is not None else None, strategy, units, note))


def view_summary(research: dict, view_count: int, *, enabled: bool = True) -> dict:
    """Count unique terminal symbols and explain the selected view on the server."""
    def tickers(items):
        return {item.get("ticker") if isinstance(item, dict) else item.ticker for item in items}

    candidates = tickers(research.get("plans", []))
    failed = tickers(research.get("failed", [])) - candidates
    filtered = tickers(research.get("filtered", [])) - failed - candidates
    total = len(research.get("plans", []))
    universe = research.get("funnel", {}).get("universe")
    if not enabled:
        state = "disabled"
    elif view_count:
        state = "candidates"
    elif total:
        state = "view_filtered"
    elif universe == 0:
        state = "empty_universe"
    elif failed and filtered:
        state = "partial_unavailable"
    elif failed:
        state = "evaluation_unavailable"
    elif filtered:
        state = "no_matching_setups"
    else:
        state = "summary_unavailable"
    messages = {
        "disabled": ("Swing research is disabled", "No setup evaluation was requested for this view.",
                     "Review the swing feature configuration; source inspection remains available."),
        "candidates": ("Research candidates", "Matching technical setups still require evidence and independent validation.",
                       "Inspect the recorded rules and missing eligibility gates."),
        "view_filtered": ("No ideas match these view filters", "Research candidates exist outside the current view.",
                          "Clear the search, setup or sector filters to see those candidates."),
        "empty_universe": ("No stocks in this research universe", "There are no selected stocks to evaluate.",
                           "Review the supplied universe and selected source snapshot."),
        "partial_unavailable": ("No setups among evaluated stocks", "Some stocks missed setup rules; others could not be evaluated.",
                                "Inspect rule rejections below and review failed stocks in Source quality."),
        "evaluation_unavailable": ("Data or evaluation unavailable", "Recorded data or evaluation failures prevented research candidates.",
                                   "Review the failure reasons below and Source quality for missing evidence."),
        "no_matching_setups": ("No matching daily setups", "The evaluated stocks did not meet the current technical setup rules.",
                               "Inspect the recorded rule checks; source eligibility and validation are separate."),
        "summary_unavailable": ("No evaluation summary available", "This response has no recorded candidate or rejection summary.",
                                "Refresh the research view to request current explanation metadata."),
    }
    title, message, action = messages[state]
    return {"state": state, "title": title, "message": message, "next_action": action,
            "total_candidates": total, "view_candidates": view_count,
            "candidate_symbols": len(candidates), "failed_symbols": len(failed),
            "filtered_symbols": len(filtered)}

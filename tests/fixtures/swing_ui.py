"""SYNTHETIC UI-only HTTP-client responses; never served by a live API."""
from copy import deepcopy
from dataclasses import asdict
import json

from app.swing.setups import evaluate
from app.swing.explanations import view_summary
from app.swing.charts import breakout_chart_context
from tests.fixtures.swing_setups import setup_fixture


def ui_payloads():
    cfg, cal, now, stock, benchmark, evidence = setup_fixture()
    evaluation = evaluate("TEST1.NS", stock, benchmark, evidence, cfg, cal, now)
    plan = json.loads(json.dumps(asdict(evaluation.plans[0]), default=str))
    vintage = "1"*64
    plan["evidence"]["vintage"] = vintage
    context = json.loads(json.dumps(asdict(breakout_chart_context(evaluation.plans[0], stock, evaluation.checks)), default=str))
    context["vintage"] = vintage
    controls = {"revision": 0, "paused": False, "alerts_paused": True, "selected_vintage": None}
    operations = {"controls": controls, "history": [], "scheduler_running": False, "swing_enabled": True,
        "replay_enabled": False, "replay_worker_running": False, "outcomes_enabled": False, "outcomes_worker_running": False,
        "outcomes_time_ist": "21:00", "webhook_configured": False, "outbox": [], "last_completed_session": "2026-10-16"}
    workspace = {"export_csv": "ticker,model_status\nTEST1.NS,RESEARCH_ONLY\n", "research": {"plans": [plan], "names": {"TEST1.NS": "SYNTHETIC Company"}, "missing_gates": ["strategy_validation"]},
        "source": {"vintage": vintage, "imported_at": "2026-10-16T12:00:00+00:00", "coverage_session": "2026-10-16",
            "required_session": "2026-10-16", "stale": True, "summary": {"universe": 1, "complete_trailing_price_histories": 0},
            "benchmark": {"ticker": "^CRSLDX", "status": "unavailable", "rows": 0, "open_rows": 0},
            "members": [{"ticker": "TEST1.NS", "delivery_status": "unavailable"}], "blockers": ["price_basis_unverified"],
            "limitations": ["SYNTHETIC UI fixture, test only"]}, "controls": controls, "scope": "SYNTHETIC test only"}
    research = workspace["research"]
    research.update(failed=[], filtered=[], funnel={"universe": 1},
        chart_contexts=[context],
        rule_explanations=[{"ticker": "TEST1.NS", "vintage": vintage, "decision_at": now.isoformat(),
            "data_session": str(stock[-1].session), "outcome": "candidate", "reasons": [],
            "checks": [asdict(check) for check in evaluation.checks]}])
    research["view_summary"] = view_summary(research, len(research["plans"]))
    chart = {"vintage": vintage, "ticker": "TEST1.NS", "price_basis": "unknown", "trade_usable": False,
        "missing_sessions": ["2026-10-14"], "observations": [
            {"session": str(row.session), **{key: getattr(row, key) for key in ("open", "high", "low", "close", "volume")}}
            for row in stock[-30:] if str(row.session) != "2026-10-14"],
        "notice": "SYNTHETIC observed reference, test only"}
    return {"operations": operations, "workspace": workspace, "vintages": [{"vintage": vintage, "imported_at": "2026-10-16T12:00:00+00:00"}],
            "paper/accounts": [], "forward/campaigns": [], "validation/reports": [], "chart": chart, "export.csv": "ticker,model_status\nTEST1.NS,RESEARCH_ONLY\n"}

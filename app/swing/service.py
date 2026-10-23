"""Read-only swing evaluation from the S1 store; no source downloads or writes."""
import json
from datetime import date, datetime

import pandas as pd

from app.core.config import Settings
from app.core.interfaces import Clock
from app.swing.models import ContextRequest, HistoryRequest, utc
from app.swing.plans import Evidence, priority
from app.swing.explanations import RuleExplanation
from app.swing.charts import breakout_chart_context
from app.swing.repository import SwingRepository
from app.swing.setups import evaluate, required_sessions


class UnknownVintage(ValueError):
    pass


class SwingResearchService:
    def __init__(self, repository: SwingRepository, config: Settings, clock: Clock, calendar):
        self.repository, self.config, self.clock, self.calendar = repository, config, clock, calendar

    def snapshot(self, vintage: str, now: datetime):
        with self.repository.connection() as conn:
            row = self.repository._visible(conn, vintage, now)
            if row is None:
                raise UnknownVintage("Unknown vintage or not yet available at decision")
            metadata = dict(row)
        try:
            self.repository.verify(vintage)
        except FileNotFoundError as exc:
            raise ValueError("Referenced immutable evidence is missing") from exc
        return metadata, json.loads(metadata["header"])

    def data_quality(self, vintage: str):
        self.snapshot(vintage, utc(self.clock.now()))
        return self.repository.report(vintage)

    def results(self, vintage: str):
        from app.providers.swing_archive import last_completed
        now = utc(self.clock.now())
        metadata, header = self.snapshot(vintage, now)
        cfg = self.config
        end = date.fromisoformat(last_completed(self.calendar, now))
        sessions = self.calendar.calendar.sessions
        trailing = sessions[sessions <= pd.Timestamp(end)][-required_sessions(cfg):]
        if len(trailing) != required_sessions(cfg):
            raise ValueError("Configured calendar cannot cover indicator warm-up")
        start = trailing[0].date()
        request = HistoryRequest(cfg.SWING_BENCHMARK, start, end, now, vintage, "raw")
        benchmark = self.repository.history(request)
        plans, failed, filtered, explanations, chart_contexts = [], [], [], [], []
        # Facts from S1 cannot become verified just because a client asks for raw.
        global_missing = ["strategy_validation"]
        if benchmark.status != "available":
            global_missing.append("benchmark:" + (benchmark.reason or benchmark.status))
        if not header.get("historical_identity_verified", False):
            global_missing.append("historical_identity_unverified")
        if not header.get("publication_verified", False):
            global_missing.append("historical_publication_unverified")
        for ticker in sorted(header["members"]):
            history = self.repository.history(HistoryRequest(ticker, start, end, now, vintage, "raw"))
            reasons = []
            if history.status != "available":
                reasons.append(history.reason or history.status)
            if benchmark.status != "available":
                reasons.append("benchmark_unavailable")
            if reasons:
                failed.append({"ticker": ticker, "strategy": None, "category": "failed", "reasons": reasons})
                explanations.append(RuleExplanation(ticker, vintage, now, end, "failed", reasons=tuple(reasons)))
                continue
            contexts = {kind: self.repository.context(ContextRequest(ticker, kind, end, now, vintage))
                        for kind in ("sector", "earnings", "surveillance", "delivery")}
            def verified(kind):
                item = contexts[kind]
                return item.status == "available" and item.publication_verified and item.available_at <= now
            # This cache adapter supplies no adjustment/identity attestations. Keep
            # those false even if a manually edited header asserts otherwise.
            evidence = Evidence(vintage, header.get("reuse_manifest_sha256") or header["adapter"],
                                datetime.fromisoformat(metadata["imported_at"]),
                                tuple(x for x in (vintage, header["universe_sha256"], header.get("reuse_manifest_sha256")) if x),
                                sector=contexts["sector"].values.get("sector") if verified("sector") else None,
                                sector_verified=verified("sector"), earnings_verified=verified("earnings"),
                                surveillance_verified=verified("surveillance"), delivery_status=contexts["delivery"].status)
            result = evaluate(ticker, history.rows, benchmark.rows, evidence, cfg, self.calendar, now)
            plans.extend(result.plans)
            chart_contexts.extend(context for plan in result.plans
                                  if (context := breakout_chart_context(plan, history.rows, result.checks)) is not None)
            outcome = "candidate" if result.plans else "failed" if any(r.category == "failed" for r in result.rejected) else "filtered"
            explanations.append(RuleExplanation(ticker, vintage, now, end, outcome, result.checks,
                tuple(dict.fromkeys(reason for rejection in result.rejected for reason in rejection.reasons))))
            for rejection in result.rejected:
                (failed if rejection.category == "failed" else filtered).append(rejection)
        return {"market": "NSE", "vintage": vintage, "decision_at": now, "data_session": end,
                "model_status": "RESEARCH_ONLY", "paper_eligible": False,
                "missing_gates": global_missing, "plans": sorted(plans, key=priority),
                "failed": failed, "filtered": filtered, "rule_explanations": explanations, "chart_contexts": chart_contexts,
                "funnel": {"universe": len(header["members"]), "plans": len(plans),
                           "failed_symbols": len({x.ticker if hasattr(x, "ticker") else x["ticker"] for x in failed}),
                           "filtered_rules": len(filtered)},
                "allocation_status": "Reconciled account/reservations require S3; quantities not exposed by this endpoint",
                "notice": "Research hypotheses only; targets are scenarios. Delayed data; no orders or validated edge."}

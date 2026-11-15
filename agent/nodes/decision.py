"""Decision (skip / watch / bid): a pure, data-driven function of the Risk and Fit reports. No LLM, no tools.

Thresholds come from config `thresholds` and `risk.skip_flags`; nothing is hard-coded. A skip always carries reasons, because
the UI shows them and the owner can override. The graph NEVER auto-sends: even `bid` only leads to a draft awaiting approval.
"""
from __future__ import annotations

import time
from typing import Any

from agent.handoffs import BidDecision, FitReport, RiskReport

from .common import Deps, fail, finish


def decide(risk: RiskReport, fit: FitReport, cfg: dict[str, Any]) -> BidDecision:
    th, rc = cfg["thresholds"], cfg["risk"]
    hard = [f for f in risk.flags if f in rc["skip_flags"]]
    if hard:
        return BidDecision(action="skip", reasons=[f"risk flag: {f}" for f in hard], confidence=min(1.0, 0.6 + risk.score / 2))
    if risk.score > th["maximum_risk_score"]:
        return BidDecision(action="skip", reasons=[f"risk score {risk.score:.2f} above {th['maximum_risk_score']}"], confidence=0.7)
    if fit.score < th["autoskip_fit_below"]:
        return BidDecision(action="skip", reasons=[f"fit {fit.score:.2f} below {th['autoskip_fit_below']}", *fit.reasons[:2]],
                           confidence=0.7)
    if fit.score < th["minimum_fit_score"]:
        return BidDecision(action="watch", reasons=[f"fit {fit.score:.2f} below {th['minimum_fit_score']}", *fit.reasons[:2]],
                           confidence=0.5)
    return BidDecision(action="bid", reasons=[f"fit {fit.score:.2f}, risk {risk.score:.2f}", *fit.reasons[:1]],
                       confidence=round(min(0.95, 0.5 + fit.score / 2 - risk.score / 2), 3))


async def decision(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        d = decide(RiskReport.model_validate(state["risk_report"]), FitReport.model_validate(state["fit_report"]), deps.cfg)
        return finish("decision", t0, {"decision": d.action, "decision_detail": d.model_dump()}, mode="deterministic", action=d.action)
    except Exception as e:
        return fail("decision", t0, e)

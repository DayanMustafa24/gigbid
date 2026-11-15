"""Pricing: fair bid and Connects value. Deterministic, data-driven (config `pricing`, rules.yaml `costs`, platform_policy in state).

Band = market percentiles from data/reference/price_stats.json (category first, then the persona's domain, then overall).
Fixed jobs: bid = share of the client's max budget, clamped into [client min, client max] and never below the freelancer's floor
(raising the floor above the client's max makes the job not worth bidding). Hourly: the freelancer's rate clamped into the client's
range; below rate x hourly_floor_factor is rejected. Connects: expected value = reply rate x win-given-reply x value x fit must exceed
cost x min_connects_value_ratio. A platform policy update (extra Connects) changes `connects_cost` here without any code change.
"""
from __future__ import annotations

import time
from typing import Any

from agent.handoffs import PriceQuote
from agent.policy.engine import load_policy

from .common import Deps, fail, finish


def market_band(job: dict[str, Any], stats: dict[str, Any], persona: str, cfg: dict[str, Any], hourly: bool) -> tuple[float | None, float | None]:
    dom = cfg["pricing"].get("persona_domain", {}).get(persona, persona)
    for blk in (stats.get("by_category", {}).get(job.get("category") or ""), stats.get("by_persona_domain", {}).get(dom),
                stats.get("overall")):
        if blk:
            lo, hi = ("hourly_p25", "hourly_p75") if hourly else ("fixed_p25", "fixed_p75")
            if blk.get(lo) is not None and blk.get(hi) is not None:
                return blk[lo], blk[hi]
    return None, None


def total_connects(job: dict[str, Any], state: dict[str, Any]) -> int:
    base = int(job.get("connects_cost") or load_policy().costs.get("connects_per_proposal_default", 0))
    extra = int(load_policy().costs.get("boosted_proposal_extra_connects", 0))
    return base + extra + int((state.get("platform_policy") or {}).get("connects_surcharge", 0))


def quote(job: dict[str, Any], fit_score: float, profile: dict[str, Any], stats: dict[str, Any], persona: str,
          cfg: dict[str, Any], connects: int, outcome: dict[str, Any] | None = None) -> PriceQuote:
    pc = cfg["pricing"]
    outcome = outcome or {}  # measured rates from outcome memory (None until enough real outcomes): replace the assumptions in config
    reply_rate = outcome.get("reply_rate")
    win_rate = outcome.get("win_given_reply")
    reply_rate = pc["base_reply_rate"] if reply_rate is None else reply_rate
    win_rate = pc["win_given_reply"] if win_rate is None else win_rate
    b = job.get("budget") or {}
    hourly = b.get("type") == "hourly"
    lo_m, hi_m = market_band(job, stats, persona, cfg, hourly)
    bmin, bmax = b.get("min"), b.get("max")
    notes: list[str] = []
    if hourly:
        rate = float(profile.get("hourly_rate_usd") or 0)
        floor = round(rate * pc["hourly_floor_factor"], 2)
        amount = rate
        if bmax is not None:
            amount = min(amount, float(bmax))
        if bmin is not None:
            amount = max(amount, float(bmin))
        value_basis = amount * pc["hourly_value_hours"]
        ok_price = amount >= floor
        if not ok_price:
            notes.append(f"client's hourly ceiling is below the freelancer's floor ({floor:.0f})")
    else:
        floor = float(profile.get("minimum_project_budget_usd") or 0)
        ref = float(bmax) if bmax is not None else float(hi_m or floor)
        amount = ref * pc["fixed_target_of_max"]
        if bmin is not None:
            amount = max(amount, float(bmin))
        if bmax is not None:
            amount = min(amount, float(bmax))
        amount = round(amount)
        value_basis = amount
        ok_price = amount >= floor
        if not ok_price:
            notes.append(f"budget cannot reach the freelancer's minimum project size ({floor:.0f})")
    cost = connects * pc["connect_price_usd"]
    ev = reply_rate * win_rate * value_basis * max(fit_score, 0.0)
    ratio = ev / cost if cost else float("inf")
    worth = ok_price and ratio >= load_policy().costs.get("min_connects_value_ratio", 1.0)
    notes.append(f"expected value ${ev:.2f} vs Connects ${cost:.2f} (ratio {ratio:.1f})" if cost else "no Connects cost")
    if outcome.get("reply_rate") is not None or outcome.get("win_given_reply") is not None:
        notes.append(f"rates measured from {outcome.get('samples', 0)} outcomes")
    if lo_m is not None:
        notes.append(f"market band {lo_m:g}-{hi_m:g}")
    return PriceQuote(amount=round(amount, 2), currency=b.get("currency", "USD"), pricing_type="hourly" if hourly else "fixed",
                      band_low=lo_m, band_high=hi_m, floor=floor, connects_cost=connects, connects_worth_it=bool(worth),
                      rationale="; ".join(notes))


async def pricing(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        job = state["job"]
        fit = float((state.get("fit_report") or {}).get("score", 0.0))
        q = quote(job, fit, deps.profile, deps.price_stats, deps.persona or state.get("persona", ""), deps.cfg,
                  total_connects(job, state), (state.get("memory_context") or {}).get("outcome"))
        return finish("pricing", t0, {"price_quote": q.model_dump()}, mode="deterministic", amount=q.amount, worth_it=q.connects_worth_it)
    except Exception as e:
        return fail("pricing", t0, e)

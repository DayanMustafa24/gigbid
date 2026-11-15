"""R&D-C (assignment): single ReAct-style agent vs LLM supervisor vs the hand-built graph, on the SAME dev jobs, SAME tools/nodes, SAME models.

    uv run python -m evals.runners.rdc_architectures --sample 50 --seed 42              # real models (Groq/Ollama keys in .env)
    uv run python -m evals.runners.rdc_architectures --offline --sample 12               # plumbing check only: scripted stand-in LLM

What differs is ONLY who controls the order of work:
  hand_graph    code (agent/graphs/bid_graph.py): verifier, critic and compliance always run; the human gate is structural.
  supervisor    an LLM picks the next worker each step (same node functions); it may skip or repeat steps.
  single_agent  one LLM with tools (risk_check, search_evidence, price_quote, verify_draft, check_policy); it decides everything.
Metrics (per architecture, same checker for all):
  decision accuracy and bid/skip F1 vs the generator's gold (by construction, never an LLM judge); scam_bid_rate (must be 0);
  raw_unsupported_per_proposal = unsupported factual claims in the FIRST draft (comparable: measures drafting + routing);
  final_unsupported_per_proposal = in the final draft by the SAME checker (zero by construction for anything that ran the verifier, so
  read it together with `verified_rate`); verified_rate (final draft passed through verification), policy_violation_rate,
  steps / tokens / latency p50 p95 per job, failure_rate (no decision or step limit). `--offline` results are labelled and prove plumbing
  only: they are NOT a comparison and must never be quoted.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from agent.graphs.bid_graph import build_bid_graph
from agent.llm import LLMRouter, ScriptedBackend
from agent.nodes.analyst import analyst
from agent.nodes.common import Deps
from agent.nodes.compliance import compliance
from agent.nodes.critic import critic
from agent.nodes.decision import decision
from agent.nodes.fit_matcher import fit_matcher
from agent.nodes.pricing import pricing, quote, total_connects
from agent.nodes.risk_assessor import risk_assessor
from agent.nodes.verifier import verifier
from agent.nodes.writer import writer
from agent.policy.engine import check_draft
from agent.runtime import build_deps
from agent.security.quarantine import neutralise, quote_untrusted
from agent.settings import load_config
from agent.state import add_usage
from evals.runners.rag_eval import PERSONAS, RESULTS, _mean, _pct, load_dev
from rag.types import PRIMARY_TYPES, JobQuery
from rag.verify import verify_and_repair

ARCHS = ("hand_graph", "supervisor", "single_agent")
WORKERS = {"analyst": analyst, "risk_assessor": risk_assessor, "fit_matcher": fit_matcher, "decision": decision, "pricing": pricing,
           "writer": writer, "verifier": verifier, "critic": critic, "compliance": compliance}


@dataclass
class RunResult:
    arch: str
    job_id: str
    decision: str | None = None
    draft: str = ""
    raw_draft: str | None = None
    raw_counts: tuple[int, int] | None = None  # (unsupported, factual) of the first draft when the architecture reports it itself
    verified: bool = False
    steps: int = 0
    tokens: int = 0
    llm_calls: int = 0
    latency_s: float = 0.0
    errors: list[str] = field(default_factory=list)
    limit_hit: bool = False


# ---- shared helpers ------------------------------------------------------------------------------------------------------------------


def merge_state(state: dict[str, Any], upd: dict[str, Any]) -> dict[str, Any]:
    """Apply a node's partial update the way the graph reducers would (errors/events append, steps add, usage sums)."""
    out = dict(state)
    for k, v in upd.items():
        if k in ("errors", "events"):
            out[k] = [*out.get(k, []), *v]
        elif k == "steps":
            out[k] = out.get(k, 0) + v
        elif k == "usage":
            out[k] = add_usage(out.get("usage"), v)
        else:
            out[k] = v
    return out


def usage_of(state: dict[str, Any]) -> tuple[int, int]:
    u = state.get("usage") or {}
    return int(u.get("tokens_in", 0) + u.get("tokens_out", 0)), int(u.get("llm_calls", 0))


def checker_pool(job: dict[str, Any], deps: Deps) -> list[dict[str, Any]]:
    """Evidence a claim may be checked against: retrieved items + primary items of the same projects (same pool as the verifier node)."""
    q = JobQuery(job["job_id"], job.get("title", ""), f"{job.get('title', '')}. {job.get('description', '')}", list(job.get("required_skills", [])))
    got = deps.retriever.retrieve(q, 5)
    ids = list(got.evidence_ids)
    for e in got.evidence_ids:
        pr = deps.corpus.by_id[e].project
        ids += [it.evidence_id for it in deps.corpus.by_project.get(pr or "", []) if it.type in PRIMARY_TYPES]
    return [{"evidence_id": e, "text": deps.corpus.by_id[e].text} for e in dict.fromkeys(ids)]


def count_claims(text: str, pool: list[dict[str, Any]], deps: Deps) -> tuple[int, int]:
    rep = verify_and_repair(text, pool, {"years_experience": deps.profile.get("years_experience")})
    return rep.unsupported_before, rep.factual_before


# ---- architecture 1: the hand-built graph --------------------------------------------------------------------------------------------


async def run_hand_graph(job: dict[str, Any], deps: Deps, graph: Any) -> RunResult:
    t0 = time.perf_counter()
    out = await graph.ainvoke({"job": job}, {"configurable": {"thread_id": f"rdc-{job['job_id']}"}})
    d = out.get("proposal_draft") or {}
    vr = out.get("verification_report")
    tok, calls = usage_of(out)
    return RunResult("hand_graph", job["job_id"], decision=out.get("decision"), draft=d.get("cover_letter", ""),
                     raw_counts=(vr["unsupported_before"], vr["factual_claims_before"]) if vr else None,
                     verified=bool(vr), steps=int(out.get("steps", 0)), tokens=tok, llm_calls=calls,
                     latency_s=time.perf_counter() - t0, errors=list(out.get("errors", [])),
                     limit_hit=bool(out.get("needs_attention") and "budget" in str((out.get("dead_letter") or {}).get("reason", ""))))


# ---- architecture 2: LLM supervisor over the same workers -----------------------------------------------------------------------------


class Route(BaseModel):
    next: str
    decision: Literal["bid", "skip", "watch"] | None = None
    reason: str = ""


def state_summary(state: dict[str, Any]) -> dict[str, Any]:
    s: dict[str, Any] = {"done": [e["node"] for e in state.get("events", [])], "errors": list(state.get("errors", []))[-2:]}
    if r := state.get("risk_report"):
        s["risk"] = {"score": r.get("score"), "flags": r.get("flags")}
    if f := state.get("fit_report"):
        s["fit"] = {"score": f.get("score"), "matched": f.get("matched_skills"), "missing": f.get("missing_skills")}
    if dd := state.get("decision_detail"):
        s["rule_decision"] = dd.get("action")
    if p := state.get("price_quote"):
        s["price"] = {"amount": p.get("amount"), "connects_worth_it": p.get("connects_worth_it")}
    if d := state.get("proposal_draft"):
        s["draft_words"] = len(d.get("cover_letter", "").split())
    if v := state.get("verification_report"):
        s["verification"] = {"unsupported_before": v.get("unsupported_before"), "removed": len(v.get("removed", []))}
    if c := state.get("critique"):
        s["critique"] = {"passed": c.get("passed"), "issues": c.get("issues", [])[:3]}
    if (cv := state.get("compliance_verdict")) is not None:
        s["compliance_allowed"] = cv.get("allowed")
    return s


async def run_supervisor(job: dict[str, Any], deps: Deps, llm: LLMRouter) -> RunResult:
    t0 = time.perf_counter()
    cap = int(deps.cfg["budgets"]["max_graph_steps"])
    state: dict[str, Any] = {"job": job, "job_id": job["job_id"], "persona": deps.persona}
    res = RunResult("supervisor", job["job_id"])
    post = quote_untrusted("job_post", f"{job.get('title', '')}\n{job.get('description', '')}")
    final: Route | None = None
    first_draft: str | None = None
    for _ in range(14):
        user = f"{post}\n<state>{json.dumps(state_summary(state))}</state>"
        try:
            route, chat = await llm.complete_json("rdc_supervisor", "rdc_supervisor", user, Route, max_tokens=200)
        except Exception as e:
            res.errors.append(f"supervisor: {type(e).__name__}")
            break
        state = merge_state(state, {"usage": chat.usage(), "steps": 1})
        if route.next == "FINISH":
            final = route
            break
        fn = WORKERS.get(route.next)
        if fn is None:
            state = merge_state(state, {"errors": [f"supervisor: unknown worker {route.next!r}"]})
            continue
        state = merge_state(state, await fn(state, deps))
        if route.next == "writer" and first_draft is None and state.get("proposal_draft"):
            first_draft = state["proposal_draft"]["cover_letter"]
        if state.get("steps", 0) >= cap:
            res.limit_hit = True
            break
    else:
        res.limit_hit = True
    d = state.get("proposal_draft") or {}
    tok, calls = usage_of(state)
    res.decision = final.decision if final else None
    res.draft, res.raw_draft = d.get("cover_letter", ""), first_draft
    nodes = [e["node"] for e in state.get("events", [])]
    last = lambda n: max((i for i, x in enumerate(nodes) if x == n), default=-1)  # noqa: E731
    res.verified = bool(state.get("verification_report")) and last("writer") >= 0 and last("verifier") > last("writer")  # verified AFTER the last draft
    res.steps, res.tokens, res.llm_calls, res.latency_s = int(state.get("steps", 0)), tok, calls, time.perf_counter() - t0
    res.errors += list(state.get("errors", []))
    return res


# ---- architecture 3: one agent with tools ----------------------------------------------------------------------------------------------


class AgentStep(BaseModel):
    action: Literal["tool", "final"]
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    decision: Literal["bid", "skip", "watch"] | None = None
    cover_letter: str = ""
    price: float | None = None
    reason: str = ""


async def run_tool(name: str, args: dict[str, Any], job: dict[str, Any], deps: Deps) -> dict[str, Any]:
    if name == "risk_check":
        r = (await risk_assessor({"job": job}, deps)).get("risk_report", {})
        return {"score": r.get("score"), "flags": r.get("flags"), "explanation": r.get("explanation")}
    if name == "search_evidence":
        q = str(args.get("query", ""))[:300]
        got = deps.retriever.retrieve(JobQuery(job["job_id"], q, q, []), 5)
        return {"evidence": [{"id": e, "text": neutralise(deps.corpus.by_id[e].text, 300)} for e in got.evidence_ids]}
    if name == "price_quote":
        q = quote(job, float(args.get("fit_score", 0.5)), deps.profile, deps.price_stats, deps.persona, deps.cfg, total_connects(job, {}))
        return {"amount": q.amount, "connects_worth_it": q.connects_worth_it, "rationale": q.rationale}
    if name == "verify_draft":
        rep = verify_and_repair(str(args.get("cover_letter", "")), checker_pool(job, deps), {"years_experience": deps.profile.get("years_experience")})
        return {"unsupported_found": rep.unsupported_before, "cleaned_letter": rep.clean_text}
    if name == "check_policy":
        v = check_draft(str(args.get("cover_letter", "")))
        return {"allowed": v.allowed, "violations": [x.rule_id for x in v.violations], "cleaned_letter": v.cleaned_cover_letter}
    return {"error": f"unknown tool {name!r}"}


async def run_single_agent(job: dict[str, Any], deps: Deps, llm: LLMRouter) -> RunResult:
    t0 = time.perf_counter()
    res = RunResult("single_agent", job["job_id"])
    transcript = [quote_untrusted("job_post", f"{job.get('title', '')}\n{job.get('description', '')}\nBudget: {json.dumps(job.get('budget'))}")]
    usage: dict[str, float] = {}
    first_draft: str | None = None
    verified_text: str | None = None
    final: AgentStep | None = None
    for _ in range(12):
        try:
            step, chat = await llm.complete_json("rdc_single", "rdc_single_agent", "\n".join(transcript), AgentStep, max_tokens=700)
        except Exception as e:
            res.errors.append(f"single_agent: {type(e).__name__}")
            break
        usage = add_usage(usage, chat.usage())
        res.steps += 1
        if step.action == "final":
            final = step
            break
        out = await run_tool(step.tool or "", step.args, job, deps)
        if step.tool in ("verify_draft", "check_policy"):
            txt = str(step.args.get("cover_letter", ""))
            first_draft = first_draft or txt
            if step.tool == "verify_draft" and "error" not in out:
                verified_text = out.get("cleaned_letter", "")
        transcript.append(f'<tool_call>{json.dumps({"tool": step.tool, "args": step.args})[:500]}</tool_call>\n<tool_result>{json.dumps(out)[:1500]}</tool_result>')
    else:
        res.limit_hit = True
    res.decision = final.decision if final else None
    res.draft = final.cover_letter if final else ""
    res.raw_draft = first_draft or (res.draft or None)
    res.verified = verified_text is not None and verified_text.strip() == res.draft.strip()  # the final text IS the verified text
    res.tokens, res.llm_calls, res.latency_s = int(usage.get("tokens_in", 0) + usage.get("tokens_out", 0)), int(usage.get("llm_calls", 0)), time.perf_counter() - t0
    return res


# ---- scoring -------------------------------------------------------------------------------------------------------------------------


def score_one(r: RunResult, job: dict[str, Any], deps: Deps) -> dict[str, Any]:
    gold = job["gold"]
    pred = "bid" if r.decision == "bid" else "skip" if r.decision in ("skip", "watch") else None
    row: dict[str, Any] = {"arch": r.arch, "job_id": r.job_id, "gold": gold["action"], "pred": pred, "scam": bool(gold.get("scam")),
                           "injection": bool(gold.get("injection")), "verified": r.verified, "steps": r.steps, "tokens": r.tokens,
                           "latency_s": r.latency_s, "failed": pred is None or r.limit_hit, "errors": len(r.errors)}
    if pred == "bid" and r.draft:
        pool = checker_pool(job, deps)
        fu, ff = count_claims(r.draft, pool, deps)
        ru, rf = r.raw_counts if r.raw_counts else (count_claims(r.raw_draft, pool, deps) if r.raw_draft else (None, None))
        v = check_draft(r.draft)
        row.update(final_unsupported=fu, final_factual=ff, raw_unsupported=ru, raw_factual=rf,
                   policy_violation=any(x.severity == "block" for x in v.violations))
    return row


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["pred"] is not None]
    tp = sum(1 for r in ok if r["pred"] == "bid" and r["gold"] == "bid")
    fp = sum(1 for r in ok if r["pred"] == "bid" and r["gold"] == "skip")
    fn = sum(1 for r in ok if r["pred"] == "skip" and r["gold"] == "bid") + sum(1 for r in rows if r["pred"] is None and r["gold"] == "bid")
    prec, rec = (tp / (tp + fp) if tp + fp else None), (tp / (tp + fn) if tp + fn else None)
    f1 = round(2 * prec * rec / (prec + rec), 4) if prec and rec else (0.0 if tp + fp + fn else None)
    drafts = [r for r in rows if "final_unsupported" in r]
    scams = [r for r in rows if r["scam"]]
    inj = [r for r in rows if r["injection"]]

    def per(key: str) -> float | None:
        vals = [r[key] for r in drafts if r.get(key) is not None]  # a draft without a measurable first version is left out, not counted as 0
        return round(sum(vals) / len(vals), 4) if vals else None

    return {
        "n_jobs": len(rows), "decision_accuracy": _mean([float(r["pred"] == r["gold"]) for r in rows]), "bid_skip_f1": f1,
        "bid_precision": None if prec is None else round(prec, 4), "bid_recall": None if rec is None else round(rec, 4),
        "scam_bid_rate": _mean([float(r["pred"] == "bid") for r in scams]), "injection_job_accuracy": _mean([float(r["pred"] == r["gold"]) for r in inj]),
        "n_drafts": len(drafts), "raw_unsupported_per_proposal": per("raw_unsupported"), "final_unsupported_per_proposal": per("final_unsupported"),
        "verified_rate": _mean([float(r["verified"]) for r in drafts]), "policy_violation_rate": _mean([float(r["policy_violation"]) for r in drafts]),
        "failure_rate": _mean([float(r["failed"]) for r in rows]), "steps_mean": _mean([float(r["steps"]) for r in rows]),
        "tokens_mean": _mean([float(r["tokens"]) for r in rows]), "latency_s_p50": _pct([r["latency_s"] for r in rows], 0.5),
        "latency_s_p95": _pct([r["latency_s"] for r in rows], 0.95),
    }


def sample_jobs(personas: list[str], n: int, seed: int) -> list[tuple[str, dict[str, Any]]]:
    """Seeded stratified sample: equal share per persona, drawn from the dev sets (never the hidden set)."""
    rng = random.Random(seed)
    per = max(1, n // len(personas))
    out: list[tuple[str, dict[str, Any]]] = []
    for p in personas:
        rows = load_dev(p)
        out += [(p, j) for j in rng.sample(rows, min(per, len(rows)))]
    return out


# ---- offline stand-in LLM (plumbing only) -----------------------------------------------------------------------------------------------


def scripted_llm(nm, system: str, user: str) -> str:
    """Deterministic stand-in that follows the happy path. Proves the harness runs end to end; says NOTHING about real model behaviour."""
    if "rdc_supervisor" in system:
        done = json.loads(user.split("<state>")[1].split("</state>")[0])["done"]
        order = ["analyst", "risk_assessor", "fit_matcher", "decision", "pricing", "writer", "verifier", "critic", "compliance"]
        nxt = next((w for w in order if w not in done), "FINISH")
        st = json.loads(user.split("<state>")[1].split("</state>")[0])
        if "decision" in done and st.get("rule_decision") != "bid":
            return json.dumps({"next": "FINISH", "decision": st.get("rule_decision", "skip")})
        return json.dumps({"next": nxt, "decision": "bid"} if nxt == "FINISH" else {"next": nxt})
    n_calls = user.count("<tool_result>")
    flags = "scam" in user.lower() or "injection" in user.lower()
    plan = ["risk_check", "search_evidence", "price_quote", "verify_draft"]
    if n_calls < len(plan):
        args = {"query": "experience"} if plan[n_calls] == "search_evidence" else {"fit_score": 0.7} if plan[n_calls] == "price_quote" else \
            {"cover_letter": "I would like to understand your goals first. What does success look like for you?"} if plan[n_calls] == "verify_draft" else {}
        return json.dumps({"action": "tool", "tool": plan[n_calls], "args": args})
    return json.dumps({"action": "final", "decision": "skip" if flags else "bid", "price": 100,
                       "cover_letter": "I would like to understand your goals first. What does success look like for you?"})


# ---- driver --------------------------------------------------------------------------------------------------------------------------


async def evaluate(archs: list[str], jobs: list[tuple[str, dict[str, Any]]], llm: LLMRouter | None, cfg: dict[str, Any]) -> dict[str, Any]:
    by_persona: dict[str, Deps] = {}
    graphs: dict[str, Any] = {}
    rows: dict[str, list[dict[str, Any]]] = {a: [] for a in archs}
    for persona, job in jobs:
        if persona not in by_persona:
            by_persona[persona] = build_deps(persona, cfg=cfg, llm=llm)
            graphs[persona] = build_bid_graph(by_persona[persona], MemorySaver())
        deps = by_persona[persona]
        orig = (job["gold"].get("duplicate_of"))
        known = [j for _, j in jobs if j["job_id"] == orig]
        deps.known_jobs = lambda known=known: known  # the original of a repost counts as already seen; nothing else does
        public = {k: v for k, v in job.items() if k not in ("gold", "synthetic")}  # architectures never see the answer key
        for a in archs:
            try:
                if a == "hand_graph":
                    r = await run_hand_graph(public, deps, graphs[persona])
                elif a == "supervisor":
                    r = await run_supervisor(public, deps, llm)
                else:
                    r = await run_single_agent(public, deps, llm)
            except Exception as e:  # one broken run must not lose the others: it is counted as a failure
                r = RunResult(a, job["job_id"], errors=[f"{type(e).__name__}: {e}"])
            rows[a].append(score_one(r, job, deps))
    return {a: {"overall": summarise(rs), "rows": rs} for a, rs in rows.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archs", nargs="*", default=list(ARCHS), choices=ARCHS)
    ap.add_argument("--personas", nargs="*", default=list(PERSONAS))
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--offline", action="store_true", help="scripted stand-in LLM: plumbing check, results labelled and not comparable")
    ap.add_argument("--stem", default=None)
    a = ap.parse_args()
    from dotenv import load_dotenv

    from agent.settings import ROOT

    load_dotenv(ROOT / ".env")
    cfg = load_config()
    llm = LLMRouter(ScriptedBackend(scripted_llm), cfg) if a.offline else LLMRouter(cfg=cfg)
    jobs = sample_jobs(a.personas, a.sample, a.seed)
    res = asyncio.run(evaluate(a.archs, jobs, llm, cfg))
    stem = a.stem or ("rdc_architectures_OFFLINE_plumbing" if a.offline else "rdc_architectures")
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": "offline-scripted-plumbing-only" if a.offline else "real-models",
               "seed": a.seed, "n_jobs": len(jobs), "personas": a.personas, "models": {n: cfg["models"]["profiles"]["open"]["nodes"].get(n)
                                                                                    for n in ("rdc_single", "rdc_supervisor", "writer", "analyst")},
               "results": {k: {"overall": v["overall"]} for k, v in res.items()}, "rows": {k: v["rows"] for k, v in res.items()}}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    cols = ["decision_accuracy", "bid_skip_f1", "scam_bid_rate", "raw_unsupported_per_proposal", "final_unsupported_per_proposal", "verified_rate",
            "policy_violation_rate", "failure_rate", "steps_mean", "tokens_mean", "latency_s_p95"]
    lines = ["| architecture | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for k, v in res.items():
        lines.append(f"| {k} | " + " | ".join("" if v["overall"][c] is None else str(v["overall"][c]) for c in cols) + " |")
    note = "<!-- OFFLINE scripted stand-in: plumbing only, NOT a comparison -->\n" if a.offline else ""
    (RESULTS / f"{stem}_table.md").write_text(note + "\n".join(lines) + "\n", encoding="utf-8")
    print(note + "\n".join(lines))


if __name__ == "__main__":
    main()

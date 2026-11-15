"""X2 proposal experiments (signature extension): do two opener strategies get different reply rates?

    uv run python -m evals.runners.x2_experiment --sample 120 --seed 42     # needs a model key (the opener only changes LLM drafts)

Design: the shipped bid graph drafts one proposal per dev job whose gold action is `bid`. The opener strategy (config `strategies`) is
assigned by hash(job_id), exactly as in production (agent/nodes/writer.pick_strategy), so each job sees ONE arm. The draft is then judged
by the GigSim stand-in client model (gigsim_local.client_model: hidden preferences, rule-based, seeded). Outcomes: replied / interview /
hired. Test: pooled two-proportion z-test on reply rate (agent/memory/stats.two_proportion_z). Dev sets only.
Caveats stated in the result file: the simulated client is a rule-based stand-in, not real people, so the finding is about the
simulator's preferences; the template fallback ignores the strategy, so without a model key there is nothing to measure and the run refuses.
Writes evals/results/x2_experiment.json and x2_experiment_table.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from agent.graphs.bid_graph import build_bid_graph
from agent.memory.stats import two_proportion_z
from agent.runtime import build_deps, default_llm
from agent.settings import ROOT, load_config
from evals.runners.rag_eval import PERSONAS, RESULTS, load_dev
from gigsim_local.client_model import decide_followup, decide_initial, hidden_prefs, score_proposal


def pick_jobs(personas: list[str], n: int, seed: int) -> list[tuple[str, dict[str, Any]]]:
    rng = random.Random(seed)
    per = max(1, n // len(personas))
    out: list[tuple[str, dict[str, Any]]] = []
    for p in personas:
        rows = [j for j in load_dev(p) if j["gold"]["action"] == "bid" and not j["gold"].get("scam")]
        out += [(p, j) for j in rng.sample(rows, min(per, len(rows)))]
    return out


def simulate(job: dict[str, Any], draft: dict[str, Any], price: float, seed: int) -> dict[str, Any]:
    """One client's verdict on one proposal. Deterministic given (seed, job)."""
    rng = random.Random(f"{seed}:x2:{job['job_id']}")
    prefs = hidden_prefs(seed, job.get("client_id", job["job_id"]), job)
    prop = {"cover_letter": draft["cover_letter"], "bid_amount": price, "screening_answers": draft.get("screening_answers", [])}
    score, factors = score_proposal(prefs, job, prop)
    replied = decide_initial(score, rng)
    stage = decide_followup("replied", score, prop["cover_letter"], rng) if replied else "ignored"
    hired = stage == "interview" and decide_followup("interview", score, prop["cover_letter"], rng) == "hired"
    return {"score": score, "replied": replied, "interview": stage == "interview", "hired": hired, "factors": factors}


async def run(jobs: list[tuple[str, dict[str, Any]]], cfg: dict[str, Any], llm: Any, seed: int) -> list[dict[str, Any]]:
    deps_by: dict[str, Any] = {}
    graphs: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for persona, job in jobs:
        if persona not in deps_by:
            deps_by[persona] = build_deps(persona, cfg=cfg, llm=llm)
            graphs[persona] = build_bid_graph(deps_by[persona], MemorySaver())
        deps_by[persona].known_jobs = lambda: []
        public = {k: v for k, v in job.items() if k not in ("gold", "synthetic")}
        try:
            out = await graphs[persona].ainvoke({"job": public}, {"configurable": {"thread_id": f"x2-{job['job_id']}"}})
        except Exception as e:  # noqa: BLE001  one broken run is counted, not fatal
            rows.append({"job_id": job["job_id"], "persona": persona, "error": f"{type(e).__name__}: {e}"})
            continue
        draft, quote = out.get("proposal_draft"), out.get("price_quote") or {}
        if out.get("decision") != "bid" or not draft or not draft.get("strategy"):
            rows.append({"job_id": job["job_id"], "persona": persona, "skipped": out.get("decision") or "no decision"})
            continue
        sim = simulate(job, draft, float(quote.get("amount") or job["budget"]["max"]), seed)
        rows.append({"job_id": job["job_id"], "persona": persona, "strategy": draft["strategy"], **{k: v for k, v in sim.items() if k != "factors"},
                     "generic_opener": sim["factors"]["generic_opener"], "words": len(draft["cover_letter"].split())})
    return rows


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    arms: dict[str, dict[str, int]] = {}
    for r in rows:
        if "strategy" not in r:
            continue
        a = arms.setdefault(r["strategy"], {"n": 0, "replied": 0, "interview": 0, "hired": 0})
        a["n"] += 1
        a["replied"] += r["replied"]
        a["interview"] += r["interview"]
        a["hired"] += r["hired"]
    res: dict[str, Any] = {"arms": {k: {**v, "reply_rate": round(v["replied"] / v["n"], 4)} for k, v in sorted(arms.items())}, "test": None,
                           "excluded": sum(1 for r in rows if "strategy" not in r)}
    if len(arms) == 2:
        (na, a), (nb, b) = sorted(arms.items())
        z, p = two_proportion_z(a["replied"], a["n"], b["replied"], b["n"])
        res["test"] = {"metric": "reply_rate", "a": na, "b": nb, "diff_a_minus_b": round(a["replied"] / a["n"] - b["replied"] / b["n"], 4),
                       "z": round(z, 4), "p_value": round(p, 4), "significant_at_0.05": p < 0.05}
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--stem", default="x2_experiment")
    a = ap.parse_args()
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    cfg = load_config()
    llm = default_llm(cfg)
    if llm is None:
        print("No model key found: the opener strategy only changes LLM drafts, so there is nothing to measure. Set GROQ_API_KEY.")
        return 1
    jobs = pick_jobs(list(PERSONAS), a.sample, a.seed)
    rows = asyncio.run(run(jobs, cfg, llm, a.seed))
    res = summarise(rows)
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": "real-models + simulated-client-standin", "seed": a.seed,
               "n_jobs": len(jobs), "strategies": cfg.get("strategies", {}), **res, "rows": rows,
               "caveat": "Client is the rule-based GigSim stand-in (gigsim_local.client_model); finding = simulator preference, not real clients."}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{a.stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = ["| strategy | n | replied | reply_rate | interview | hired |", "|---|---|---|---|---|---|"]
    lines += [f"| {k} | {v['n']} | {v['replied']} | {v['reply_rate']} | {v['interview']} | {v['hired']} |" for k, v in res["arms"].items()]
    if res["test"]:
        t = res["test"]
        lines.append(f"\nz = {t['z']}, p = {t['p_value']} (two-proportion z-test on reply rate; {t['a']} minus {t['b']} = {t['diff_a_minus_b']})")
    lines.append(f"\nexcluded jobs (no bid/draft/error): {res['excluded']}")
    (RESULTS / f"{a.stem}_table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

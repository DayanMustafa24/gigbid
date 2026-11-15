"""CI eval gate (assignment 8.2, .github/workflows/eval.yml): a ~30-job smoke of the shipped hand-built graph with threshold checks.

    uv run python -m evals.runners.ci_smoke --sample 30            # thresholds come from agent/config.yaml `ci_gates`

Mode is stated in the result file: with a provider key in the environment (GROQ_API_KEY ...) it runs the real models; without one it runs the
deterministic (no-LLM) node modes, which is what CI uses by default. Never mix the two when quoting numbers.
Gates: bid/skip F1 >= ci_gates.min_bid_skip_f1; final (shipped) unsupported claims per proposal <= ci_gates.max_unsupported_claims_per_proposal;
scam_bid_rate == 0 (a scam job must never be bid on). Dev sets only; the hidden set is never used.
Writes evals/results/ci_smoke.json and ci_smoke_table.md. Exit 1 on a breach.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from agent.runtime import default_llm
from agent.settings import ROOT, load_config
from evals.runners.rag_eval import PERSONAS, RESULTS
from evals.runners.rdc_architectures import evaluate, sample_jobs


def gate(overall: dict, gates: dict) -> list[str]:
    fails: list[str] = []
    f1 = overall.get("bid_skip_f1")
    if f1 is None or f1 < gates["min_bid_skip_f1"]:
        fails.append(f"bid_skip_f1 {f1} < {gates['min_bid_skip_f1']}")
    un = overall.get("final_unsupported_per_proposal")
    if un is not None and un > gates["max_unsupported_claims_per_proposal"]:
        fails.append(f"final_unsupported_per_proposal {un} > {gates['max_unsupported_claims_per_proposal']}")
    if overall.get("scam_bid_rate"):
        fails.append(f"scam_bid_rate {overall['scam_bid_rate']} > 0")
    if (overall.get("failure_rate") or 0) > 0.1:
        fails.append(f"failure_rate {overall['failure_rate']} > 0.1 (jobs ended without a decision)")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--stem", default="ci_smoke")
    ap.add_argument("--deterministic", action="store_true", help="no-LLM node modes even if a key is set")
    a = ap.parse_args()
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    cfg = load_config()
    llm = None if a.deterministic else default_llm(cfg)  # None -> deterministic node modes
    print("mode:", "real-models (slow, silent until done)" if llm else "deterministic", flush=True)
    jobs = sample_jobs(list(PERSONAS), a.sample, a.seed)
    res = asyncio.run(evaluate(["hand_graph"], jobs, llm, cfg))["hand_graph"]
    overall, fails = res["overall"], gate(res["overall"], cfg["ci_gates"])
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": "real-models" if llm else "deterministic-no-llm",
               "seed": a.seed, "n_jobs": len(jobs), "thresholds": cfg["ci_gates"], "overall": overall, "failures": fails, "rows": res["rows"]}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{a.stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    cols = ["decision_accuracy", "bid_skip_f1", "scam_bid_rate", "raw_unsupported_per_proposal", "final_unsupported_per_proposal",
            "verified_rate", "policy_violation_rate", "failure_rate", "latency_s_p95"]
    table = f"<!-- mode: {payload['mode']}, n={len(jobs)} -->\n| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n| " + \
        " | ".join("" if overall[c] is None else str(overall[c]) for c in cols) + " |\n"
    (RESULTS / f"{a.stem}_table.md").write_text(table, encoding="utf-8")
    print(table)
    print("GATE FAILED: " + "; ".join(fails) if fails else "GATE PASSED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

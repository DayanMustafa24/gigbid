"""Ablation (R&D-B): the best pipeline with each enabled component switched off, one at a time, vs V0 and the full pipeline.

    uv run python -m evals.runners.ablation [--personas ...] [--limit N] [--llm]

Best flags come from config `rag.best` (edit after reading the table). Output: evals/results/rag_ablation{.json,_table.*}.
Reading it: a component's value = (full - without it). Per-persona and per-bucket breakdowns are in the JSON.
"""
from __future__ import annotations

import argparse

from agent.settings import load_config
from evals.runners.rag_eval import PERSONAS, evaluate, write_outputs
from rag.registry import PRESETS, ablations, best_flags


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--personas", nargs="*", default=list(PERSONAS))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--llm", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    best = best_flags(cfg)
    variants = {"v0_naive": PRESETS["v0_naive"], "full": best, **ablations(best)}
    payload = evaluate(variants, a.personas, limit=a.limit, use_llm=a.llm, cfg=cfg)
    write_outputs(payload, "rag_ablation", mode="llm" if a.llm else "no-llm")
    print(open("evals/results/rag_ablation_table.md", encoding="utf-8").read())


if __name__ == "__main__":
    main()

"""Retrieval evaluation (assignment B4/B5). One command regenerates the table:

    uv run python -m evals.runners.rag_eval [--personas ai_ml ...] [--variants v0_naive vbest ...] [--limit N] [--llm]

Gold comes from the dev sets (built by construction, never an LLM judge). Definitions (also written into the JSON):
  recall@5            |top5 ∩ gold| / min(5, |gold|), item level; gold = union of the job's requirement evidence ids that
                      exist in the corpus. Gold lists EVERY item of a matching project, so item recall alone rewards
                      returning five items of one project.
  project_recall@5    share of gold PROJECTS covered by the top 5 (the diversity-aware metric; V1 targets this).
  mrr, ndcg@10        binary relevance on items. Retrieval runs once at k=10; @5 metrics use the first 5.
  abstain_unfit/fit   share of jobs with no gold evidence (should abstain) / with gold evidence (should not) on which the
                      retriever returned nothing. Only V8/vbest can abstain.
Jobs without gold requirements are excluded from ranking metrics. Latency = retrieval only (not LLM drafting).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agent.settings import load_config
from rag.registry import all_variants, build_context
from rag.types import JobQuery
from rag.variants.pipeline import ComposedRetriever, Flags

ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "evals" / "datasets"
RESULTS = ROOT / "evals" / "results"
PERSONAS = ("ai_ml", "fullstack_web", "data_analyst")


def load_dev(persona: str) -> list[dict]:
    with open(DATASETS / f"dev_{persona}.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def project_of(eid: str) -> str:
    return eid.split(":")[1] if eid.count(":") >= 2 else eid


def job_metrics(ranked: list[str], gold: set[str]) -> dict[str, float]:
    top5 = ranked[:5]
    hit5 = len(set(top5) & gold)
    gp = {project_of(g) for g in gold}
    first = next((i for i, e in enumerate(ranked[:10], 1) if e in gold), None)
    dcg = sum(1 / math.log2(i + 1) for i, e in enumerate(ranked[:10], 1) if e in gold)
    idcg = sum(1 / math.log2(i + 1) for i in range(1, min(10, len(gold)) + 1))
    return {
        "recall@5": hit5 / min(5, len(gold)),
        "project_recall@5": len({project_of(e) for e in top5} & gp) / len(gp),
        "mrr": 1 / first if first else 0.0,
        "ndcg@10": dcg / idcg if idcg else 0.0,
    }


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, math.ceil(q * len(s)) - 1)], 2)


def summarise(rows: list[dict]) -> dict[str, Any]:
    ranked = [r for r in rows if r["gold_n"] > 0]
    unfit = [r for r in rows if r["gold_n"] == 0]
    out: dict[str, Any] = {
        "n_jobs": len(rows), "n_ranked": len(ranked),
        "abstain_unfit": _mean([float(r["abstained"]) for r in unfit]),
        "abstain_fit": _mean([float(r["abstained"]) for r in ranked]),
        "latency_ms_p50": _pct([r["latency_ms"] for r in rows], 0.5),
        "latency_ms_p95": _pct([r["latency_ms"] for r in rows], 0.95),
        "llm_calls": sum(r["llm_calls"] for r in rows),
    }
    for m in ("recall@5", "project_recall@5", "mrr", "ndcg@10"):
        out[m] = _mean([r[m] for r in ranked])
    return out


def evaluate(variants: dict[str, Flags], personas: tuple[str, ...] | list[str], *, limit: int | None = None,
             use_llm: bool = False, cfg: dict | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    pdir = ROOT / cfg["rag"].get("personas_dir", "data/synthetic/personas")
    llm = None
    if use_llm:
        from rag.llm import OpenAICompatLLM

        llm = OpenAICompatLLM("analyst")
    per_variant: dict[str, dict[str, list[dict]]] = {v: {} for v in variants}
    info: dict[str, Any] = {}
    for persona in personas:
        ctx = build_context(pdir / persona, cfg, llm)
        info[persona] = {"corpus_items": len(ctx.corpus.items), "embedder": ctx.embedder.name,
                         "reranker": ctx.reranker.name}
        dev = load_dev(persona)[:limit]
        for vname, flags in variants.items():
            retr = ComposedRetriever(vname, Flags(**{**asdict(flags), "llm": use_llm}), ctx)
            rows = []
            for jr in dev:
                gold_all = {e for rq in jr["gold"].get("requirements", []) for e in rq.get("evidence_ids", [])}
                gold = {e for e in gold_all if e in ctx.corpus.by_id}
                res = retr.retrieve(JobQuery.from_dev_row(jr), 10)
                row: dict[str, Any] = {
                    "job_id": jr["job_id"], "gold_n": len(gold), "abstained": res.abstained,
                    "latency_ms": res.meta["latency_ms"], "llm_calls": res.meta["llm_calls"],
                    "n_req": len(jr["gold"].get("requirements", [])),
                    "type": ("injection" if jr["gold"].get("injection") else
                             "duplicate" if jr["gold"].get("duplicate_of") else "standard"),
                }
                if gold:
                    row.update(job_metrics(res.evidence_ids, gold))
                rows.append(row)
            per_variant[vname][persona] = rows
            if hasattr(ctx.embedder, "flush"):
                ctx.embedder.flush()
    results: dict[str, Any] = {}
    for v, per in per_variant.items():
        allrows = [r for rows in per.values() for r in rows]
        by_bucket: dict[str, list[dict]] = defaultdict(list)
        for r in allrows:
            if r["gold_n"]:
                by_bucket[f"reqs={min(r['n_req'], 3)}{'+' if r['n_req'] >= 3 else ''}"].append(r)
                by_bucket[f"type={r['type']}"].append(r)
        results[v] = {
            "flags": asdict(variants[v]),
            "overall": summarise(allrows),
            "by_persona": {p: summarise(rows) for p, rows in per.items()},
            "by_bucket": {b: summarise(rows) for b, rows in sorted(by_bucket.items())},
        }
    return {"setup": info, "results": results}


def write_outputs(payload: dict, stem: str, k: int = 5, mode: str = "") -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "k": k, "mode": mode, **payload}
    (RESULTS / f"{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    cols = ["recall@5", "project_recall@5", "mrr", "ndcg@10", "abstain_unfit", "abstain_fit", "latency_ms_p95"]
    with open(RESULTS / f"{stem}_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["variant", *cols])
        for v, r in payload["results"].items():
            w.writerow([v, *[r["overall"][c] for c in cols]])
    lines = ["| variant | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for v, r in payload["results"].items():
        lines.append(f"| {v} | " + " | ".join("" if r["overall"][c] is None else str(r["overall"][c]) for c in cols) + " |")
    setup = "; ".join(f"{p}: {i['embedder']} / {i['reranker']}" for p, i in payload["setup"].items())
    (RESULTS / f"{stem}_table.md").write_text(f"<!-- {setup} -->\n" + "\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--personas", nargs="*", default=list(PERSONAS))
    ap.add_argument("--variants", nargs="*", default=None, help="default: all v0..v8 + vbest")
    ap.add_argument("--limit", type=int, default=None, help="first N dev jobs per persona (smoke runs)")
    ap.add_argument("--llm", action="store_true", help="use the configured LLM in V1/V6/V7/V8 where supported")
    ap.add_argument("--stem", default="rag_eval")
    a = ap.parse_args()
    cfg = load_config()
    variants = all_variants(cfg)
    if a.variants:
        variants = {v: variants[v] for v in a.variants}
    payload = evaluate(variants, a.personas, limit=a.limit, use_llm=a.llm, cfg=cfg)
    write_outputs(payload, a.stem, mode="llm" if a.llm else "no-llm")
    print((RESULTS / f"{a.stem}_table.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

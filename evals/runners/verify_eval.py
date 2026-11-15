"""Claim-verification evaluation (assignment B3/B4): unsupported claims per proposal BEFORE and AFTER verification.

    uv run python -m evals.runners.verify_eval [--personas ...] [--limit N]

Data: each persona's past proposals (150 each). Gold per sentence comes from the generator's claim labels:
  supported_by=<project>  -> truthful (should be kept)       fabricated=true -> false claim (should be removed)
  supported_by=None, not fabricated (generic puffery)         -> excluded from detection metrics
Two evidence settings: `oracle` = all evidence of the project(s) the proposal truthfully cites (upper bound for the
verifier alone), `retrieved` = top-5 from the best pipeline for the proposal's job (end-to-end, includes retrieval misses).
Deterministic verifier only (no LLM). Fabricated claims are the planted ones (~8% of proposals); see DATA_CARD.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent.settings import load_config
from evals.runners.rag_eval import PERSONAS, RESULTS, ROOT
from rag.registry import best_flags, build_context
from rag.types import PRIMARY_TYPES, JobQuery
from rag.variants.pipeline import ComposedRetriever
from rag.verify import split_claims, verify_and_repair, verify_claims


def _gold(sentence: str, claims: list[dict]) -> str | None:
    for c in claims:
        if sentence in c["text"]:
            return "fabricated" if c.get("fabricated") else "truthful" if c.get("supported_by") else None
    return None


def _div(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def run(persona: str, ctx, retr, limit: int | None) -> dict[str, Any]:
    path = Path(ctx.corpus.source) / "proposals.jsonl"
    props = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()][:limit]
    out: dict[str, Any] = {}
    facts = {"years_experience": ctx.corpus.profile.get("years_experience")}
    for mode in ("oracle", "retrieved"):
        tp = fp = fn = kept_true = total_true = 0
        fab_before = fab_after = unsup_before = unsup_after = factual_before = factual_after = 0
        for p in props:
            if mode == "oracle":
                projs = [c["supported_by"] for c in p["claims"] if c.get("supported_by")]
                ev = [{"evidence_id": it.evidence_id, "text": it.text} for pr in projs for it in ctx.corpus.by_project.get(pr, [])]
            else:
                job = p["job"]
                desc = f"{job['title']}. {job.get('problem_hint', '')}. Industry: {job.get('industry', '')}."
                q = JobQuery(p["proposal_id"], job["title"], desc, list(job.get("skills", [])))
                got = retr.retrieve(q, 5)
                # same evidence pool as the live verifier node (agent/nodes/verifier.py): retrieved items + sibling items of their projects
                ids = list(got.evidence_ids)
                for e in got.evidence_ids:
                    pr = ctx.corpus.by_id[e].project
                    ids += [it.evidence_id for it in ctx.corpus.by_project.get(pr or "", []) if it.type in PRIMARY_TYPES]
                ev = [{"evidence_id": e, "text": ctx.corpus.by_id[e].text} for e in dict.fromkeys(ids)]
            rep = verify_and_repair(p["text"], ev, facts)
            rows = verify_claims(p["text"], ev, facts)
            clean = set(split_claims(rep.clean_text))
            for r in rows:
                g = _gold(r["text"], p["claims"]) if r["kind"] == "factual" else None
                flagged = r["label"] == "unsupported"
                if g == "fabricated":
                    tp += flagged
                    fn += not flagged
                    fab_before += 1
                    fab_after += r["text"] in clean
                elif g == "truthful":
                    fp += flagged
                    total_true += 1
                    kept_true += r["text"] in clean
            factual_before += rep.factual_before
            factual_after += rep.factual_after
            unsup_before += rep.unsupported_before
            unsup_after += rep.unsupported_after
        n = len(props)
        out[mode] = {
            "proposals": n,
            "unsupported_flagged_per_proposal_before": _div(unsup_before, n),
            "unsupported_flagged_per_proposal_after": _div(unsup_after, n),
            "unsupported_rate_before": _div(unsup_before, factual_before),
            "unsupported_rate_after": _div(unsup_after, factual_after),
            "fabricated_per_proposal_before": _div(fab_before, n),
            "fabricated_per_proposal_after": _div(fab_after, n),
            "fabrication_precision": _div(tp, tp + fp),
            "fabrication_recall": _div(tp, tp + fn),
            "truthful_claims_kept": _div(kept_true, total_true),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--personas", nargs="*", default=list(PERSONAS))
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    cfg = load_config()
    pdir = ROOT / cfg["rag"].get("personas_dir", "data/synthetic/personas")
    res: dict[str, Any] = {}
    for persona in a.personas:
        ctx = build_context(pdir / persona, cfg)
        res[persona] = run(persona, ctx, ComposedRetriever("vbest", best_flags(cfg), ctx), a.limit)
        res[persona]["setup"] = {"embedder": ctx.embedder.name, "reranker": ctx.reranker.name}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "rag_verify_eval.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()

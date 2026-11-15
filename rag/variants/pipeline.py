"""One composable retrieval pipeline; the assignment's variants V0..V8 are presets (flags) over it.

Why composable: R&D-B asks to "ablate your best pipeline one component at a time". With flags, the ablation is a loop
over `replace(best, <flag>=False)`, and every variant is the same code path (differences are the point being measured).

Stages (each switchable):
  query      decompose (one query per requirement) | hyde (hypothetical proposal as dense query) | whole post
  retrieve   dense cosine | hybrid (dense + BM25 fused with RRF)           over units of one VIEW:
  view       fixed chunks | multi-vector project views (summary/readme/code) | contextual chunks (context line prepended)
  rank       cross-encoder rerank | outcome-aware boost from past proposal outcomes
  merge      requirement-diverse merge (each requirement gets its own best evidence before filling by RRF)
  correct    corrective loop: grade evidence per requirement, re-query exact terms when weak, abstain when nothing backs it
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..corpus import Corpus, context_line
from ..embeddings import Embedder, LexicalReranker, Reranker
from ..index import UnitIndex, rrf
from ..llm import LLM, load_prompt
from ..requirements import extract_requirements, extract_requirements_llm, mentions, term_of
from ..types import Item, JobQuery, Retrieval

DEFAULT_PARAMS: dict[str, Any] = {
    "candidates": 30,       # units fetched per query before reranking / merging
    "rrf_k": 60,
    "chunk_chars": 500,
    "chunk_overlap": 80,
    "outcome_beta": 0.25,   # max +-25% score change from outcome signal
    "max_requery": 1,       # corrective loop rounds
    "abstain": True,        # corrective: return nothing when no requirement is backed
}


@dataclass(frozen=True)
class Flags:
    decompose: bool = False
    hybrid: bool = False
    rerank: bool = False
    outcome: bool = False
    multivector: bool = False
    contextual: bool = False
    hyde: bool = False
    corrective: bool = False
    llm: bool = False  # use the LLM where a component supports it (needs a configured provider)


class RagContext:
    """Everything a retriever needs, built once per persona: corpus, models, shared indexes."""

    def __init__(self, corpus: Corpus, embedder: Embedder, reranker: Reranker | None = None, llm: LLM | None = None,
                 params: dict[str, Any] | None = None):
        self.corpus, self.embedder, self.llm = corpus, embedder, llm
        self.reranker = reranker or LexicalReranker()
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        self._indexes: dict[tuple, UnitIndex] = {}
        self._ctx: dict[str, str] = {}
        self._boost: dict[str, float] | None = None

    def index(self, key: tuple, units_fn: Callable[[], list]) -> UnitIndex:
        if key not in self._indexes:
            self._indexes[key] = UnitIndex(units_fn(), self.embedder)
        return self._indexes[key]

    def fixed_units(self, context: Callable[[Item], str] | None = None):
        p = self.params
        return self.corpus.units_fixed(p["chunk_chars"], p["chunk_overlap"], context)

    def boost(self) -> dict[str, float]:
        if self._boost is None:
            self._boost = self.corpus.outcome_boost()
        return self._boost

    def context_fn(self, use_llm: bool) -> Callable[[Item], str]:
        def fn(it: Item) -> str:
            base = context_line(it)
            if not (use_llm and self.llm):
                return base
            if it.evidence_id not in self._ctx:
                proj = "\n".join(i.text for i in self.corpus.by_project.get(it.project or "", []) if i.type == "case_study")
                try:
                    sys_p, _ = load_prompt("rag_context")
                    txt = self.llm.complete(sys_p, f"<project>{proj[:700]}</project>\n<fragment>{it.text[:600]}</fragment>",
                                            max_tokens=80)
                except Exception:  # context is an enhancement: fall back to the deterministic line
                    txt = ""
                self._ctx[it.evidence_id] = " ".join(txt.split())
            return f"{base} {self._ctx[it.evidence_id]}".strip()

        return fn


class ComposedRetriever:
    def __init__(self, name: str, flags: Flags, ctx: RagContext):
        self.name, self.f, self.ctx = name, flags, ctx
        if flags.multivector:
            self.index = ctx.index(("mv",), ctx.corpus.units_multivector)
        elif flags.contextual:
            self.index = ctx.index(("ctx", flags.llm), lambda: ctx.fixed_units(ctx.context_fn(flags.llm)))
        else:
            self.index = ctx.index(("fixed",), ctx.fixed_units)
        self._fixed = ctx.index(("fixed",), ctx.fixed_units) if flags.corrective else None

    # ---- helpers ---------------------------------------------------------------------------------------------------
    def _proj(self, eid: str) -> str | None:
        it = self.ctx.corpus.by_id.get(eid)
        return it.project if it else None

    def _hyde(self, job: JobQuery) -> tuple[str, str]:
        if self.f.llm and self.ctx.llm:
            try:
                sys_p, _ = load_prompt("rag_hyde")
                return self.ctx.llm.complete(sys_p, f"<job_post>\n{job.whole_post[:1500]}\n</job_post>", max_tokens=200), "llm"
            except Exception:
                pass
        skills = ", ".join(job.required_skills) or job.title
        return (f"{job.title}. I have hands-on experience with {skills}. On a similar project I used {skills} "
                f"and delivered measurable results for the client."), "template"

    def _rank_query(self, dtext: str, ltext: str) -> list[tuple[str, float]]:
        f, p, idx = self.f, self.ctx.params, self.index
        n = p["candidates"]
        d = idx.dense(dtext)
        if f.hybrid:
            fused = rrf([idx.rank(d, n), idx.rank(idx.bm25(ltext), n, positive_only=True)], k=p["rrf_k"])
            order = sorted(fused, key=lambda i: (-fused[i], idx.units[i].uid))[:n]
            scores = {i: fused[i] for i in order}
        else:
            order = idx.rank(d, n)
            scores = {i: float(d[i]) for i in order}
        if f.rerank and order:
            rs = self.ctx.reranker.score(ltext, [idx.units[i].text for i in order])
            pairs = sorted(zip(order, rs, strict=False), key=lambda x: (-x[1], idx.units[x[0]].uid))
            order, scores = [i for i, _ in pairs], {i: s for i, s in pairs}
        if f.outcome and order:
            boost, beta = self.ctx.boost(), p["outcome_beta"]
            lo, hi = min(scores.values()), max(scores.values())
            span = (hi - lo) or 1.0
            adj = {i: ((scores[i] - lo) / span + 0.05) * (1 + beta * boost.get(idx.units[i].project or "", 0.0))
                   for i in order}
            order = sorted(order, key=lambda i: (-adj[i], idx.units[i].uid))
            scores = adj
        return self._to_items(order, scores)

    def _to_items(self, order: list[int], scores: dict[int, float]) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        seen: set[str] = set()
        for i in order:
            u = self.index.units[i]
            eids = list(u.evidence_ids)
            if self.f.multivector and u.project:  # return the whole project, matched view first
                eids += [e for e in self.ctx.corpus.project_items(u.project) if e not in eids]
            for e in eids:
                if e not in seen:
                    seen.add(e)
                    out.append((e, float(scores[i])))
        return out

    def _diverse_merge(self, per: list[list[tuple[str, float]]], weights: list[float], k: int) -> list[tuple[str, float]]:
        chosen: list[str] = []
        covered: set[str | None] = set()
        for lst in per:  # pass 1: every requirement gets its own best evidence, preferring a new project
            pick = next((e for e, _ in lst if e not in chosen and self._proj(e) not in covered), None) or next(
                (e for e, _ in lst if e not in chosen), None)
            if pick and len(chosen) < k:
                chosen.append(pick)
                covered.add(self._proj(pick))
        fused = rrf([[e for e, _ in lst] for lst in per], k=self.ctx.params["rrf_k"], weights=weights)
        for e in sorted(fused, key=lambda e: (-fused[e], e)):  # pass 2: fill by fused rank
            if len(chosen) >= k:
                break
            if e not in chosen:
                chosen.append(e)
        return [(e, fused.get(e, 0.0)) for e in chosen[:k]]

    # ---- corrective loop (V8) --------------------------------------------------------------------------------------
    def _supported(self, reqs, ids: list[str]) -> dict[str, bool]:
        texts = [self.ctx.corpus.by_id[e].text for e in ids if e in self.ctx.corpus.by_id]
        return {r.id: any(mentions(t, term_of(r)) for t in texts) for r in reqs}

    def _llm_grade(self, req, ids: list[str]) -> bool:
        if not (self.f.llm and self.ctx.llm):
            return False
        try:
            sys_p, _ = load_prompt("rag_grade")
            ev = "\n---\n".join(self.ctx.corpus.by_id[e].text for e in ids if e in self.ctx.corpus.by_id)[:1500]
            ans = self.ctx.llm.complete(sys_p, f"<requirement>{req.text}</requirement>\n<evidence>{ev}</evidence>", max_tokens=5)
            return ans.strip().lower().startswith("yes")
        except Exception:
            return False

    def _correct(self, reqs, chosen: list[tuple[str, float]], k: int):
        ids = [e for e, _ in chosen]
        sup = self._supported(reqs, ids)
        rounds = 0
        while not all(sup.values()) and rounds < self.ctx.params["max_requery"]:
            rounds += 1
            assert self._fixed is not None
            for r in reqs:
                if sup[r.id]:
                    continue
                term = term_of(r)
                for i in self._fixed.rank(self._fixed.bm25(term), self.ctx.params["candidates"], positive_only=True):
                    u = self._fixed.units[i]
                    if mentions(u.text, term) and u.evidence_ids[0] not in ids:
                        ids = [u.evidence_ids[0]] + ids[: k - 1]
                        break
            sup = self._supported(reqs, ids)
        for r in reqs:  # optional LLM grader for requirements the lexical check could not back
            if not sup[r.id] and self._llm_grade(r, ids):
                sup[r.id] = True
        unsupported = [term_of(r) for r in reqs if not sup[r.id]]
        abstain = self.ctx.params["abstain"] and not any(sup.values())
        score = dict(chosen)
        kept = [] if abstain else [(e, score.get(e, 0.0)) for e in ids]
        return kept, abstain, {"unsupported_requirements": unsupported, "requery_rounds": rounds}

    # ---- public ----------------------------------------------------------------------------------------------------
    def retrieve(self, job: JobQuery, k: int = 5) -> Retrieval:
        t0 = time.perf_counter()
        f = self.f
        calls0 = getattr(self.ctx.llm, "calls", 0) if self.ctx.llm else 0
        if (f.decompose or f.corrective) and f.llm and self.ctx.llm:
            reqs = extract_requirements_llm(job, self.ctx.llm)
        else:
            reqs = extract_requirements(job) if (f.decompose or f.corrective) else []
        meta: dict[str, Any] = {"variant": self.name, "embedder": self.ctx.embedder.name,
                                "reranker": self.ctx.reranker.name if f.rerank else None}
        if f.decompose and reqs:
            queries = [(term_of(r), term_of(r), 1.0 if r.must_have else 0.6) for r in reqs]
            per = [self._rank_query(dt, lt) for dt, lt, _ in queries]
            chosen = self._diverse_merge(per, [w for _, _, w in queries], k)
            meta["queries"] = [q[0] for q in queries]
        else:
            post = job.whole_post
            if f.hyde:
                hypo, mode = self._hyde(job)
                meta["hyde_mode"] = mode
                chosen = self._rank_query(hypo, f"{post}\n{hypo}")[:k]
            else:
                chosen = self._rank_query(post, post)[:k]
            meta["queries"] = ["<whole post>"]
        abstained = False
        if f.corrective and reqs:
            chosen, abstained, extra = self._correct(reqs, chosen, k)
            meta.update(extra)
        meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        meta["llm_calls"] = (getattr(self.ctx.llm, "calls", 0) if self.ctx.llm else 0) - calls0
        ids = [e for e, _ in chosen]
        projects = list(dict.fromkeys(p for p in (self._proj(e) for e in ids) if p))
        return Retrieval(ids, [s for _, s in chosen], projects, abstained, meta)

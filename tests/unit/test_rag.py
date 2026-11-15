"""Phase 4 unit tests: ingestion, index, variants, verification, metrics. Offline (hashing embedder, no LLM)."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from evals.runners.rag_eval import ROOT, evaluate, job_metrics
from rag.corpus import fixed_chunks
from rag.embeddings import CachedEmbedder, HashingEmbedder, LexicalReranker
from rag.index import UnitIndex, rrf
from rag.ingest.loaders import load_persona
from rag.registry import DEFAULT_BEST, PRESETS, ablations, best_flags, build_retriever
from rag.types import JobQuery
from rag.variants.pipeline import ComposedRetriever, Flags, RagContext
from rag.variants.v0_naive import retrieve as v0_retrieve
from rag.verify import split_claims, verify_and_repair, verify_claims

CS_A = ("---\nproject: rag-bot\ndate: 2025-03-01\nclient_industry: SaaS\n---\n# Support assistant for a SaaS helpdesk\n\n"
        "Built a RAG chatbot over help articles. The assistant deflected 38 percent of tier one tickets within two months.\n")
CS_B = ("---\nproject: churn\ndate: 2022-05-01\nclient_industry: Telecom\n---\n# Churn model for a telecom\n\n"
        "Trained an XGBoost churn model with feature engineering. It lifted campaign ROI by 22 percent.\n")
CS_C = ("---\nproject: dash\ndate: 2023-02-01\nclient_industry: Retail\n---\n# Sales dashboards for a retailer\n\n"
        "Built Tableau dashboards for weekly sales review. Managers cut reporting time in half.\n")


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def persona_dir(tmp_path: Path) -> Path:
    d = tmp_path / "mini"
    _w(d / "profile.json", json.dumps({"skills": ["RAG", "XGBoost", "Tableau", "pgvector"], "years_experience": 6}))
    for slug, cs in (("rag-bot", CS_A), ("churn", CS_B), ("dash", CS_C)):
        _w(d / "case_studies" / f"{slug}.md", cs)
    _w(d / "repos" / "rag-bot" / "README.md", "# rag-bot\n\nSetup: pip install -r requirements.txt\n")
    _w(d / "repos" / "rag-bot" / "src" / "main.py", "from rank_bm25 import BM25Okapi\nimport psycopg  # pgvector store\n")
    _w(d / "cv.md", "# Test\n\n## Selected projects\n"
       "- Support assistant for a SaaS helpdesk (2025): RAG chatbot over help articles; deflected 38 percent of tickets.\n"
       "- Churn model for a telecom (2022): XGBoost churn model; lifted campaign ROI by 22 percent.\n\n## Skills\nRAG\n")
    _w(d / "reviews.jsonl", json.dumps({"project": "rag-bot", "rating": 5.0, "date": "2025-03-02",
                                        "client_industry": "SaaS", "text": "Great work, the pgvector side was handled."}) + "\n")
    props = [
        {"proposal_id": "mini-prop-001", "date": "2025-04-01", "job": {"industry": "SaaS"}, "outcome": "won",
         "text": "On a similar project the assistant deflected 38 percent of tier one tickets within two months."},
        {"proposal_id": "mini-prop-002", "date": "2025-04-02", "job": {"industry": "Retail"}, "outcome": "ignored",
         "text": "Hello, I am a hardworking professional and can start immediately."},
    ]
    _w(d / "proposals.jsonl", "".join(json.dumps(p) + "\n" for p in props))
    return d


@pytest.fixture()
def ctx(persona_dir: Path) -> RagContext:
    return RagContext(load_persona(persona_dir), HashingEmbedder(256), LexicalReranker(), params={"candidates": 20})


def job(skills: list[str], desc: str = "") -> JobQuery:
    return JobQuery("j1", "Need help", desc or ("We need: " + ", ".join(skills)), skills)


# ---- ingestion ------------------------------------------------------------------------------------------------------
def test_scan_loads_every_type_and_metadata(persona_dir: Path) -> None:
    c = load_persona(persona_dir)
    types = {it.type for it in c.items}
    assert {"case_study", "readme", "code", "cv_bullet", "review", "proposal"} <= types
    a = c.by_id["mini:rag-bot:case_study"]
    assert a.client_industry == "SaaS" and a.date == "2025-03-01" and "RAG" in a.skills
    assert a.project_title == "Support assistant for a SaaS helpdesk"
    assert "pgvector" in c.by_id["mini:rag-bot:code"].skills or "pgvector" in c.by_id["mini:rag-bot:review"].skills


def test_proposals_linked_to_projects_by_text_and_outcome_boost(persona_dir: Path) -> None:
    c = load_persona(persona_dir)
    assert c.proposal_links["mini-prop-001"] == ["rag-bot"] and c.proposal_links["mini-prop-002"] == []
    boost = c.outcome_boost()
    assert boost["rag-bot"] > 0


def test_corpus_diff_detects_changes(persona_dir: Path) -> None:
    old = load_persona(persona_dir)
    _w(persona_dir / "case_studies" / "dash.md", CS_C + "\nAdded a Power BI migration.\n")
    _w(persona_dir / "case_studies" / "new.md", CS_B.replace("churn", "fraud"))
    d = old.diff(load_persona(persona_dir))
    assert "mini:dash:case_study" in d["changed"] and d["removed"] == [] and any("fraud" in x for x in d["added"])


def test_fixed_chunks_windows() -> None:
    text = " ".join(f"word{i}" for i in range(300))
    ch = fixed_chunks(text, 200, 40)
    assert len(ch) > 3 and all(len(c) <= 200 for c in ch) and fixed_chunks("short") == ["short"]


# ---- embeddings / index ---------------------------------------------------------------------------------------------
def test_hashing_embedder_deterministic_and_normalised() -> None:
    e = HashingEmbedder(128)
    a, b = e.embed(["XGBoost churn model"]), e.embed(["XGBoost churn model"])
    assert np.allclose(a, b) and abs(float(np.linalg.norm(a[0])) - 1) < 1e-5
    sim = lambda x, y: float(e.embed([x])[0] @ e.embed([y])[0])  # noqa: E731
    assert sim("xgboost churn", "xgboost churn model") > sim("xgboost churn", "tableau dashboards")


def test_cached_embedder_hits_and_persists(tmp_path: Path) -> None:
    c = CachedEmbedder(HashingEmbedder(64), tmp_path)
    c.embed(["a b c", "d e f"])
    c.embed(["a b c"])
    assert c.hits == 1 and c.misses == 2
    c.flush()
    c2 = CachedEmbedder(HashingEmbedder(64), tmp_path)
    c2.embed(["a b c"])
    assert c2.hits == 1 and c2.misses == 0


def test_index_bm25_exact_term_and_rrf(ctx: RagContext) -> None:
    idx = ctx.index(("fixed",), ctx.fixed_units)
    top = idx.rank(idx.bm25("xgboost"), 1, positive_only=True)
    assert idx.units[top[0]].project == "churn"
    assert list(rrf([["a", "b"], ["b", "a"]]))[0] in ("a", "b") and rrf([["a"], ["a"]])["a"] > rrf([["a"]])["a"]
    assert isinstance(idx, UnitIndex) and len(idx) > 0


def test_v0_function_signature_returns_dedup_items(ctx: RagContext) -> None:
    idx = ctx.index(("fixed",), ctx.fixed_units)
    hits = v0_retrieve("xgboost churn", idx, 3)
    assert len(hits) == 3 and len({h["evidence_id"] for h in hits}) == 3


# ---- variants -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(PRESETS) + ["vbest"])
def test_every_variant_runs_and_returns_known_ids(ctx: RagContext, name: str) -> None:
    r = build_retriever(name, ctx).retrieve(job(["RAG", "XGBoost"]), 5)
    assert all(e in ctx.corpus.by_id for e in r.evidence_ids) and len(r.evidence_ids) <= 5
    assert r.meta["latency_ms"] >= 0 and r.meta["variant"] == name


def test_proposals_never_returned_as_evidence(ctx: RagContext) -> None:
    for name in ("v0_naive", "v5_multivector", "vbest"):
        r = build_retriever(name, ctx).retrieve(job(["RAG"]), 10)
        assert not any(e.endswith(("prop-001", "prop-002")) for e in r.evidence_ids)


def test_hybrid_finds_exact_tool(ctx: RagContext) -> None:
    r = ComposedRetriever("t", Flags(hybrid=True), ctx).retrieve(job([], "need xgboost model"), 3)
    assert r.projects[0] == "churn"


def test_decompose_covers_both_requirements(ctx: RagContext) -> None:
    r = ComposedRetriever("t", Flags(decompose=True, hybrid=True), ctx).retrieve(job(["RAG", "XGBoost"]), 5)
    assert {"rag-bot", "churn"} <= set(r.projects)
    assert r.meta["queries"] == ["RAG", "XGBoost"]


def test_multivector_returns_project_bundle(ctx: RagContext) -> None:
    r = ComposedRetriever("t", Flags(multivector=True, hybrid=True), ctx).retrieve(job([], "need xgboost model"), 5)
    assert r.projects[0] == "churn" and "mini:churn:case_study" in r.evidence_ids


def test_corrective_abstains_on_unfit_and_flags_unsupported(ctx: RagContext) -> None:
    v8 = ComposedRetriever("t", PRESETS["v8_corrective"], ctx)
    unfit = v8.retrieve(job(["Kotlin", "Verilog"]), 5)
    assert unfit.abstained and unfit.evidence_ids == []
    fit = v8.retrieve(job(["RAG", "XGBoost"]), 5)
    assert not fit.abstained and fit.meta["unsupported_requirements"] == []
    mixed = v8.retrieve(job(["RAG", "Verilog"]), 5)
    assert not mixed.abstained and mixed.meta["unsupported_requirements"] == ["Verilog"]


def test_outcome_flag_changes_scores_not_ids_validity(ctx: RagContext) -> None:
    r = ComposedRetriever("t", Flags(outcome=True), ctx).retrieve(job(["RAG"]), 5)
    assert r.evidence_ids and all(s >= 0 for s in r.scores)


def test_contextual_units_carry_context_line(ctx: RagContext) -> None:
    r = ComposedRetriever("t", Flags(contextual=True), ctx)
    assert any("SaaS client" in u.text for u in r.index.units)


def test_ablations_and_best_flags() -> None:
    abl = ablations(DEFAULT_BEST)
    assert set(abl) == {"-decompose", "-hybrid", "-rerank", "-corrective"}
    assert abl["-hybrid"].hybrid is False and abl["-hybrid"].decompose is True
    assert best_flags({"rag": {"best": {"hybrid": True}}}) == replace(Flags(), hybrid=True)


def test_unknown_variant_raises(ctx: RagContext) -> None:
    with pytest.raises(KeyError):
        build_retriever("v99", ctx)


# ---- verification ---------------------------------------------------------------------------------------------------
EVIDENCE = [{"evidence_id": "a", "text": CS_A}]
DRAFT = ("Quick question about your goals? On the project 'Support assistant for a SaaS helpdesk', I built a RAG chatbot and "
         "the assistant deflected 38 percent of tier one tickets within two months. I led a team of 25 engineers at a "
         "Fortune 500 company. I take quality seriously and communicate daily. When suits you?")


def test_split_and_kinds() -> None:
    rows = verify_claims(DRAFT, EVIDENCE)
    assert [r["kind"] for r in rows] == ["question", "factual", "factual", "other", "question"]
    assert len(split_claims(DRAFT)) == 5


def test_verify_labels_and_removes_fabrication() -> None:
    rows = verify_claims(DRAFT, EVIDENCE)
    assert rows[1]["label"] == "supported" and rows[2]["label"] == "unsupported"
    rep = verify_and_repair(DRAFT, EVIDENCE)
    assert any("Fortune" in r for r in rep.removed) and "Fortune" not in rep.clean_text
    assert rep.unsupported_before == 1 and rep.unsupported_after == 0 and rep.rate_before > rep.rate_after
    assert "RAG chatbot" in rep.clean_text and "communicate daily" in rep.clean_text


def test_verify_trims_unbacked_number_clause() -> None:
    draft = "I built a RAG chatbot that deflected 38 percent of tickets and saved 900 hours."
    rep = verify_and_repair(draft, EVIDENCE)
    assert "900" not in rep.clean_text and "38" in rep.clean_text and len(rep.rewritten) == 1


def test_verify_years_checked_against_profile_facts() -> None:
    claim = "I have 12 years of experience with this technology."
    assert verify_claims(claim, EVIDENCE, {"years_experience": 6})[0]["label"] == "unsupported"
    assert verify_claims("I have 5 years of experience with RAG.", EVIDENCE, {"years_experience": 6})[0]["label"] != "unsupported"


def test_percent_forms_are_equivalent() -> None:
    ev = [{"evidence_id": "a", "text": "The assistant deflected 38% of tier one tickets within two months."}]
    row = verify_claims("I built an assistant that deflected 38 percent of tier one tickets.", ev)[0]
    assert row["label"] == "supported"


def test_llm_requirement_extractor_and_fallback(ctx: RagContext) -> None:
    from rag.llm import ScriptedLLM
    from rag.requirements import extract_requirements_llm

    j = JobQuery("j", "t", "Need Python and dbt help", [])
    ok = extract_requirements_llm(j, ScriptedLLM(lambda s, u: 'Sure: ["Python", "dbt models"]'))
    assert [r.text for r in ok] == ["Experience with Python", "Experience with dbt models"]
    bad = extract_requirements_llm(JobQuery("j", "t", "x", ["RAG"]), ScriptedLLM(lambda s, u: "no json"))
    assert [r.text for r in bad] == ["Experience with RAG"]
    r = ComposedRetriever("t", Flags(decompose=True, llm=True), RagContext(
        ctx.corpus, ctx.embedder, ctx.reranker, ScriptedLLM(lambda s, u: '["RAG", "XGBoost"]'))).retrieve(j, 5)
    assert r.meta["queries"] == ["RAG", "XGBoost"]


# ---- metrics --------------------------------------------------------------------------------------------------------
def test_job_metrics_math() -> None:
    gold = {"a:p1:x", "a:p2:y", "a:p3:z"}
    m = job_metrics(["a:p1:x", "a:p2:y", "q:q:q"], gold)
    assert m["recall@5"] == pytest.approx(2 / 3) and m["project_recall@5"] == pytest.approx(2 / 3) and m["mrr"] == 1.0
    assert m["ndcg@10"] == pytest.approx((1 + 1 / np.log2(3)) / (1 + 1 / np.log2(3) + 0.5))


# ---- real synthetic personas (skipped when `make seed` has not run) ------------------------------------------------
REAL = ROOT / "data" / "synthetic" / "personas" / "ai_ml"


@pytest.mark.skipif(not (REAL / "evidence.jsonl").exists(), reason="run `make seed` first")
def test_real_persona_loads_without_answer_key_and_eval_smoke() -> None:
    c = load_persona(REAL)
    assert len(c.primary()) > 50 and len(c.proposals()) == 150
    assert not any("gold_skills" in repr(it) for it in c.items)
    out = evaluate({"v0_naive": PRESETS["v0_naive"], "vbest": DEFAULT_BEST}, ["ai_ml"], limit=20)
    for v in ("v0_naive", "vbest"):
        o = out["results"][v]["overall"]
        assert o["n_ranked"] > 0 and 0 <= o["recall@5"] <= 1 and 0 <= o["project_recall@5"] <= 1

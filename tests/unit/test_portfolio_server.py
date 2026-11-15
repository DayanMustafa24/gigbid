"""Portfolio store + MCP wiring. Fixture persona is built in tmp_path; two tests also run on the real synthetic data."""
import json

import pytest

from mcp_servers.portfolio.store import DEFAULT_DIR, EvidenceFilters, PortfolioError, PortfolioStore

CASE = """---
persona: p1
type: case_study
project: rag-bot
skills: [RAG, FastAPI]
date: 2025-07-20
client_industry: SaaS
---
# Support assistant

## Result
The work deflected 38% of tier-1 tickets. Built retrieval with pgvector and reranking.
"""
README = "# rag-bot\nFastAPI service with pgvector hybrid search and a reranker.\n"
OTHER = "---\ntype: case_study\nproject: churn\n---\n# Churn model\nXGBoost with SHAP explanations for a telco.\n"


@pytest.fixture
def root(tmp_path):
    p = tmp_path / "p1"
    (p / "case_studies").mkdir(parents=True)
    (p / "repos" / "rag-bot").mkdir(parents=True)
    (p / "case_studies" / "rag-bot.md").write_text(CASE, encoding="utf-8")
    (p / "case_studies" / "churn.md").write_text(OTHER, encoding="utf-8")
    (p / "repos" / "rag-bot" / "README.md").write_text(README, encoding="utf-8")
    rows = [
        {"evidence_id": "p1:rag-bot:case_study", "path": "case_studies/rag-bot.md", "project": "rag-bot",
         "type": "case_study", "date": "2025-07-20", "client_industry": "SaaS", "gold_skills": ["SECRET_GOLD"]},
        {"evidence_id": "p1:rag-bot:readme", "path": "repos/rag-bot/README.md", "project": "rag-bot",
         "type": "readme", "date": "2025-07-20", "client_industry": "SaaS", "gold_skills": ["SECRET_GOLD"]},
        {"evidence_id": "p1:churn:case_study", "path": "case_studies/churn.md", "project": "churn",
         "type": "case_study", "date": "2022-03-11", "client_industry": "Telecom", "gold_skills": ["XGBoost"]},
    ]
    (p / "evidence.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    (p / "profile.json").write_text(json.dumps({
        "display_name": "Test P", "headline": "ML", "hourly_rate_usd": 50, "minimum_project_budget_usd": 200,
        "skills": ["RAG", "FastAPI", "Kubernetes", "SHAP"], "synthetic": True}), encoding="utf-8")
    (p / "reviews.jsonl").write_text(json.dumps({"project": "rag-bot", "rating": 4.8, "text": "Great.", "date": "2025-08-01"}), encoding="utf-8")
    (p / "proposals.jsonl").write_text(json.dumps({
        "proposal_id": "p1-prop-001", "date": "2025-01-01", "bid_price": 300, "outcome": "won", "text": "Hi.",
        "job": {"title": "Need RAG"}, "claims": [{"supported_by": None, "text": "x"}],
        "features": {"contains_fabricated_claim": True}}), encoding="utf-8")
    return tmp_path


def test_search_ranks_relevant_first_and_filters(root):
    s = PortfolioStore(root)
    r = s.search_evidence("pgvector reranking for support tickets", k=3)
    assert r.results[0].evidence_id == "p1:rag-bot:case_study"
    assert "pgvector" in r.results[0].snippet.lower()
    assert all(h.project == "rag-bot" for h in r.results)  # churn has no matching term -> not returned
    only_readme = s.search_evidence("pgvector", filters=EvidenceFilters(types=["readme"]))
    assert [h.type for h in only_readme.results] == ["readme"]
    assert s.search_evidence("xgboost", filters=EvidenceFilters(industry="saas")).results == []
    assert s.search_evidence("xgboost", filters=EvidenceFilters(since="2023-01-01")).results == []
    assert s.search_evidence("shap", filters=EvidenceFilters(skills=["SHAP"])).results[0].project == "churn"


def test_no_evidence_means_empty_and_blank_is_error(root):
    s = PortfolioStore(root)
    assert s.search_evidence("quantum blockchain kubernetes").results == []
    with pytest.raises(PortfolioError):
        s.search_evidence("   ")


def test_gold_labels_never_leak(root):
    s = PortfolioStore(root)
    dump = (
        s.search_evidence("rag").model_dump_json()
        + s.get_case_study("rag-bot").model_dump_json()
        + s.list_skills().model_dump_json()
        + s.get_past_proposal("p1-prop-001").model_dump_json()
    )
    assert "SECRET_GOLD" not in dump and "fabricated" not in dump and "supported_by" not in dump


def test_case_study_by_slug_or_evidence_id(root):
    s = PortfolioStore(root)
    a, b = s.get_case_study("rag-bot"), s.get_case_study("p1:rag-bot:readme")
    assert a.project == b.project == "rag-bot" and a.title == "Support assistant"
    assert a.metadata["client_industry"] == "SaaS" and a.reviews[0].rating == 4.8
    assert a.evidence_ids == ["p1:rag-bot:case_study", "p1:rag-bot:readme"]
    with pytest.raises(PortfolioError, match="known projects"):
        s.get_case_study("nope")


def test_list_skills_separates_proven_from_unproven(root):
    out = PortfolioStore(root).list_skills()
    assert {x.skill for x in out.evidenced} >= {"RAG", "FastAPI", "SHAP"}
    assert out.declared_without_evidence == ["Kubernetes"]


def test_profile_and_proposal_and_persona_resolution(root):
    s = PortfolioStore(root)
    assert s.get_profile().hourly_rate_usd == 50
    pr = s.get_past_proposal("p1-prop-001")
    assert pr.outcome == "won" and pr.job_title == "Need RAG"
    with pytest.raises(PortfolioError, match="unknown freelancer_id"):
        s.get_profile("someone_else")
    with pytest.raises(PortfolioError):
        s.get_past_proposal("p1-prop-999")


def test_multiple_personas_require_an_id(root):
    other = root / "p2"
    other.mkdir()
    (other / "evidence.jsonl").write_text("", encoding="utf-8")
    s = PortfolioStore(root)
    with pytest.raises(PortfolioError, match="freelancer_id is required"):
        s.get_profile()
    assert s.get_profile("p1").freelancer_id == "p1"


# ---- real synthetic data (skipped when `make seed` has not run) ----------------------------------------------------

real = pytest.mark.skipif(not (DEFAULT_DIR / "ai_ml" / "evidence.jsonl").exists(), reason="synthetic personas not built")


@real
def test_real_persona_finds_rag_case_study():
    s = PortfolioStore()
    r = s.search_evidence("build a RAG chatbot with citations over support articles", k=5, freelancer_id="ai_ml")
    assert r.results and any(h.project == "support-rag-bot" for h in r.results)
    assert s.get_case_study("support-rag-bot", "ai_ml").reviews
    assert len(s.freelancers()) == 3


@real
def test_real_persona_has_unevidenced_skill_report():
    out = PortfolioStore().list_skills("ai_ml")
    assert out.evidenced and len(out.evidenced) + len(out.declared_without_evidence) == len(out.declared_skills)


# ---- MCP wiring (in-memory client <-> server) ---------------------------------------------------------------------


async def test_mcp_tools_listed_with_read_only_annotations_and_callable(root):
    memory = pytest.importorskip("mcp.shared.memory")
    from mcp_servers.portfolio.server import create_server

    server = create_server(PortfolioStore(root))
    async with memory.create_connected_server_and_client_session(server._mcp_server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {"search_evidence", "get_case_study", "list_skills", "get_profile"}
        assert all(t.annotations and t.annotations.readOnlyHint for t in tools.values())
        assert all(t.outputSchema for t in tools.values())
        res = await client.call_tool("search_evidence", {"requirement": "pgvector", "k": 2})
        assert not res.isError and res.structuredContent["results"][0]["evidence_id"].startswith("p1:rag-bot")
        bad = await client.call_tool("get_case_study", {"case_id": "nope"})
        assert bad.isError
        uris = [str(t.uriTemplate) for t in (await client.list_resource_templates()).resourceTemplates]
        assert "portfolio://case/{case_id}" in uris

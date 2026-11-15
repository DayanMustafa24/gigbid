"""Portfolio upload storage: safe paths, caps, ingestible by the RAG loader, private to its owner."""
from __future__ import annotations

import pytest

from agent.runtime import persona_visible, user_persona_id
from apps.api import portfolio as pf
from rag.ingest.loaders import load_persona

CASE = "# Legal RAG assistant\n\nBuilt a RAG assistant with pgvector for a legal team; cut ticket handling time by 38%."


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "ROOT", tmp_path)
    return pf.base_dir("Owner-123-abc")


def test_saved_files_are_ingested_by_the_rag_loader(base):
    pf.write_profile(base, {"display_name": "Sam", "skills": ["RAG", "pgvector", "RAG"], "years_experience": 5})
    assert pf.read_profile(base)["skills"] == ["RAG", "pgvector"]  # de-duplicated, order kept
    assert not pf.is_ready(base)
    pf.save_file(base, "case_study", "Legal RAG", CASE)
    pf.save_file(base, "cv", "ignored", "## Experience\n- Built a RAG assistant with pgvector for a legal team")
    assert pf.is_ready(base)
    corpus = load_persona(base)
    cs = next(i for i in corpus.items if i.type == "case_study")
    assert cs.project == "legal-rag" and "RAG" in cs.skills and corpus.profile["display_name"] == "Sam"
    assert {f["kind"] for f in pf.list_files(base)} == {"case_study", "cv"}


def test_names_cannot_escape_the_portfolio_folder(base):
    pf.write_profile(base, {"display_name": "Sam", "skills": []})
    pf.save_file(base, "case_study", "../../../etc/passwd", CASE)
    written = [p for p in base.rglob("*") if p.is_file() and p.name != "profile.json"]
    assert written and all(base in p.parents for p in written) and written[0].name == "etc-passwd.md"
    assert pf.delete_file(base, "case_study", "../../../etc/passwd") and pf.list_files(base) == []
    assert pf.delete_file(base, "case_study", "missing") is False


def test_replace_and_limits(base, monkeypatch):
    pf.write_profile(base, {"display_name": "Sam", "skills": []})
    assert pf.save_file(base, "readme", "api", CASE)["replaced"] is False
    assert pf.save_file(base, "readme", "api", CASE + " more")["replaced"] is True
    monkeypatch.setattr(pf, "MAX_FILES", 1)
    with pytest.raises(ValueError, match="at most"):
        pf.save_file(base, "case_study", "second", CASE)
    monkeypatch.setattr(pf, "MAX_FILES", 200)
    monkeypatch.setattr(pf, "MAX_TOTAL_CHARS", 50)
    with pytest.raises(ValueError, match="size limit"):
        pf.save_file(base, "case_study", "big", CASE)


def test_a_user_portfolio_is_private_to_its_owner():
    mine = user_persona_id("Owner-123-abc")
    assert mine.startswith("user-") and persona_visible(mine, "Owner-123-abc")
    assert not persona_visible(mine, "someone-else-entirely")
    assert persona_visible("ai_ml", "anyone")  # synthetic demo personas are shared

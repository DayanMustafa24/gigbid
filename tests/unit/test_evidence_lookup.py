"""Evidence lookup for the UI: citable types only, de-duplicated, capped, unknown ids ignored."""
from __future__ import annotations

import json
from pathlib import Path

from apps.api import evidence


def _persona(tmp_path: Path) -> Path:
    d = tmp_path / "p1"
    (d / "case_studies").mkdir(parents=True)
    (d / "case_studies" / "rag-bot.md").write_text(
        "---\nproject: rag-bot\ndate: 2025-03-01\nclient_industry: SaaS\n---\n# Support assistant\n\n" + "Built a RAG chatbot. " * 120, encoding="utf-8")
    (d / "profile.json").write_text(json.dumps({"skills": ["RAG"]}), encoding="utf-8")
    (d / "proposals.jsonl").write_text(json.dumps({"proposal_id": "p1-prop-001", "text": "Hello there from a past proposal.",
                                                   "outcome": "won", "job": {"industry": "SaaS"}}) + "\n", encoding="utf-8")
    return d


def test_lookup_serves_citable_items_only(tmp_path: Path) -> None:
    evidence._corpus.cache_clear()
    d = _persona(tmp_path)
    rows = evidence.lookup(d, ["p1:rag-bot:case_study", "p1:rag-bot:case_study", "p1-prop-001", "nope"])
    assert [r["evidence_id"] for r in rows] == ["p1:rag-bot:case_study"]  # proposal and unknown id dropped, duplicate collapsed
    r = rows[0]
    assert r["type"] == "case_study" and r["client_industry"] == "SaaS" and r["project_title"] == "Support assistant"
    assert len(r["text"]) == evidence.MAX_TEXT and r["truncated"] is True

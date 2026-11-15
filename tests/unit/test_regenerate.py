"""Regenerate-one-paragraph: candidate only, verified against evidence, policy-checked, other paragraphs untouched."""
from __future__ import annotations

import json

import pytest

from agent.llm import LLMRouter, ScriptedBackend
from agent.nodes.common import Deps
from agent.nodes.regenerate import RegenerateError, regenerate_paragraph, split_paragraphs

EV = [{"evidence_id": "e1", "text": "Built RAG with pgvector and FastAPI for a legal client", "type": "case_study", "project": "proj1"}]
STATE = {"job": {"job_id": "j1", "title": "RAG bot", "description": "Need a RAG chatbot."}, "evidence": EV,
         "proposal_draft": {"job_id": "j1", "cover_letter": "Hello Sam, thanks for posting.\n\nSecond paragraph about work.\n\nCould we talk this week?",
                            "screening_answers": []}}
PROFILE = {"years_experience": 6}


def deps(reply):
    return Deps(llm=LLMRouter(ScriptedBackend(lambda nm, s, u: reply)), profile=PROFILE)


def test_split_paragraphs():
    assert split_paragraphs("a\n\n\n b \n\nc") == ["a", "b", "c"]


async def test_replaces_only_the_chosen_paragraph():
    reply = json.dumps({"paragraph": "I built a RAG system with pgvector.", "evidence_ids": ["e1", "zzz"]})
    out = await regenerate_paragraph(STATE, deps(reply), 1, "shorter")
    paras = split_paragraphs(out["cover_letter"])
    assert paras[0].startswith("Hello Sam") and paras[1] == "I built a RAG system with pgvector." and paras[2].startswith("Could we")
    assert out["evidence_ids"] == ["e1"] and out["violations"] == [] and out["paragraph"] == 1


async def test_unsupported_claims_never_come_back():
    reply = json.dumps({"paragraph": "I increased revenue by 400% for Google.", "evidence_ids": []})
    with pytest.raises(RegenerateError) as e:
        await regenerate_paragraph(STATE, deps(reply), 1)
    assert e.value.code == "nothing_supported"


async def test_errors_are_explicit():
    with pytest.raises(RegenerateError) as e:
        await regenerate_paragraph(STATE, deps("{}"), 9)
    assert e.value.code == "paragraph_out_of_range"
    with pytest.raises(RegenerateError) as e2:
        await regenerate_paragraph(STATE, Deps(profile=PROFILE), 0)
    assert e2.value.code == "model_unavailable"
    with pytest.raises(RegenerateError) as e3:
        await regenerate_paragraph(STATE, deps("garbage"), 0)
    assert e3.value.code == "model_failed"


async def test_contact_details_in_the_new_paragraph_are_redacted():
    reply = json.dumps({"paragraph": "I built a RAG system with pgvector. Email me at a@b.co.", "evidence_ids": ["e1"]})
    out = await regenerate_paragraph(STATE, deps(reply), 1)
    assert "a@b.co" not in out["cover_letter"]

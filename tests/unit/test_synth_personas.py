import json
import re

import pytest

from scripts.synth.build_personas import build
from scripts.synth.personas import PERSONAS


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("personas")
    for p in PERSONAS.values():
        build(p, 7, d)
    return d


def rows(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_counts_match_assignment(out):
    for pid in PERSONAS:
        m = json.loads((out / pid / "manifest.json").read_text())
        assert 15 <= m["case_studies"] <= 25
        assert m["proposals"] == 150
        assert set(m["outcomes"]) == {"won", "replied", "ignored"}
        assert m["outcomes"]["ignored"] > m["outcomes"]["won"]


def test_deterministic(out, tmp_path):
    p = PERSONAS["ai_ml"]
    build(p, 7, tmp_path)
    for f in ("proposals.jsonl", "evidence.jsonl", "cv.md"):
        assert (tmp_path / "ai_ml" / f).read_bytes() == (out / "ai_ml" / f).read_bytes()


def test_evidence_gold_and_labels(out):
    for pid, persona in PERSONAS.items():
        ev = rows(out / pid / "evidence.jsonl")
        assert all(e["synthetic"] for e in ev)
        assert len({e["evidence_id"] for e in ev}) == len(ev)
        slugs = {p.slug for p in persona.projects}
        for e in ev:
            if e["type"] != "proposal":
                assert e["project"] in slugs and e["gold_skills"] and e["date"] and e["client_industry"]
                assert set(e["named_skills"]) <= set(e["gold_skills"])
            else:
                assert e["outcome"] in {"won", "replied", "ignored"}


def test_implied_skills_hidden_from_case_study_prose(out):
    ev = rows(out / "ai_ml" / "evidence.jsonl")
    cs = next(e for e in ev if e["evidence_id"] == "ai_ml:support-rag-bot:case_study")
    assert "pgvector" not in cs["named_skills"] and "pgvector" in cs["gold_skills"]
    code = next(e for e in ev if e["evidence_id"] == "ai_ml:support-rag-bot:code")
    assert "pgvector" in code["named_skills"]


def test_proposals_contain_no_contact_details_and_fabrications_are_labelled(out):
    for pid in PERSONAS:
        props = rows(out / pid / "proposals.jsonl")
        assert not any(re.search(r"@|whatsapp|telegram|paypal", p["text"], re.I) for p in props)
        for p in props:
            fab = any(c.get("fabricated") for c in p["claims"])
            assert fab == p["features"]["contains_fabricated_claim"]
            assert all(c["supported_by"] is None for c in p["claims"] if c.get("fabricated"))

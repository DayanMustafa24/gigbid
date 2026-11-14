import json

import pytest

from scripts.synth import build_jobs
from scripts.synth.build_personas import build
from scripts.synth.personas import PERSONAS


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    d = tmp_path_factory.mktemp("p")
    for p in PERSONAS.values():
        build(p, 7, d)
    return {pid: build_jobs.build_set(p, d, {}, 7, "dev", build_jobs.DEV_MIX) for pid, p in PERSONAS.items()}, d


def test_dev_mix_and_labels(sets):
    data, _ = sets
    for jobs, golds, clients in data.values():
        assert len(jobs) == 200 == len(golds) == len(clients)
        assert sum(g["action"] == "bid" for g in golds) == 120
        assert sum(g["scam"] for g in golds) == 20
        assert sum(g["injection"] for g in golds) == 10
        assert sum(g["duplicate_of"] is not None for g in golds) == 10
        assert len({j["job_id"] for j in jobs}) == 200


def test_gold_evidence_is_consistent(sets):
    data, d = sets
    for pid, (_jobs, golds, _) in data.items():
        ids = {json.loads(x)["evidence_id"] for x in (d / pid / "evidence.jsonl").read_text().splitlines()}
        for g in golds:
            for r in g["requirements"]:
                assert r["evidence_ids"] and set(r["evidence_ids"]) <= ids
            if g["action"] == "bid":
                assert g["requirements"] and g["fair_band"][0] < g["fair_band"][1]


def test_injection_text_present_and_scams_unverified(sets):
    data, _ = sets
    for jobs, golds, clients in data.values():
        for j, g, c in zip(jobs, golds, clients, strict=True):
            blob = j["description"] + " ".join(j["screening_questions"])
            if g["injection"]:
                assert build_jobs.INJECTIONS[g["injection_kind"]].rstrip(".") in blob
            if g["scam"]:
                assert c["payment_verified"] is False


def test_unfit_skills_not_in_portfolio(sets):
    data, _ = sets
    for pid, (jobs, golds, _) in data.items():
        have = set(PERSONAS[pid].all_skills())
        for j, g in zip(jobs, golds, strict=True):
            if g["reason"] == "skills outside portfolio":
                assert not set(j["required_skills"]) & have

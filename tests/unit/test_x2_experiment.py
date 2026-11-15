"""X2 proposal experiments: the statistics and the simulator seam (no model, no network)."""
from apps.api.routers.analytics import strategy_experiment
from evals.runners.x2_experiment import simulate, summarise

JOB = {"job_id": "j1", "client_id": "c1", "title": "RAG chatbot", "description": "Build a RAG chatbot with prompt engineering.",
       "required_skills": ["RAG", "prompt engineering"], "budget": {"currency": "USD", "min": 300, "max": 500, "type": "fixed"},
       "screening_questions": ["Describe a RAG project."]}
DRAFT = {"cover_letter": "I built and deployed a RAG chatbot and reduced support load by 30 percent. " * 4, "screening_answers": ["Did one."]}


def test_simulate_is_deterministic():
    assert simulate(JOB, DRAFT, 400.0, 7) == simulate(JOB, DRAFT, 400.0, 7)


def test_summarise_counts_arms_and_tests_difference():
    rows = [{"strategy": "a", "replied": True, "interview": False, "hired": False}] * 30 + \
           [{"strategy": "a", "replied": False, "interview": False, "hired": False}] * 10 + \
           [{"strategy": "b", "replied": True, "interview": False, "hired": False}] * 10 + \
           [{"strategy": "b", "replied": False, "interview": False, "hired": False}] * 30 + [{"skipped": "skip"}]
    res = summarise(rows)
    assert res["arms"]["a"]["reply_rate"] == 0.75 and res["arms"]["b"]["reply_rate"] == 0.25
    assert res["test"]["significant_at_0.05"] is True and res["excluded"] == 1


def test_no_test_with_a_single_arm():
    assert summarise([{"strategy": "a", "replied": True, "interview": False, "hired": False}])["test"] is None


def test_api_experiment_pools_personas_and_handles_empty():
    pooled = strategy_experiment({"p1": {"a": {"n": 20, "replied": 10, "won": 1}, "b": {"n": 20, "replied": 4, "won": 0}},
                                  "p2": {"a": {"n": 20, "replied": 12, "won": 2}, "b": {"n": 20, "replied": 6, "won": 1}}})
    assert pooled["arms"]["a"]["n"] == 40 and pooled["test"]["p_value"] < 0.05
    assert strategy_experiment({}) == {"arms": {}, "test": None}

import asyncio

from agent.llm import LLMRouter, ScriptedBackend
from agent.settings import load_config
from evals.runners.rda_tools import ARMS, FINE_SPLIT, TASKS, args_ok, build_tools, evaluate, load_shipped, oracle


def test_forty_tasks_and_every_gold_tool_exists_in_the_shipped_set():
    shipped = load_shipped()
    assert len(TASKS) == 40
    assert all(t[1] in shipped for t in TASKS)
    fine_names = {f for v in FINE_SPLIT.values() for f, _ in v}
    assert all(t[3] in fine_names for t in TASKS if t[3])
    assert all(b in shipped for b in FINE_SPLIT)


def test_arms_differ_in_size_as_designed():
    shipped = load_shipped()
    n = {a: len(build_tools(a, shipped).splitlines()) for a in ARMS}
    assert n["coarse"] == 4 and n["best"] == n["terse"] == len(shipped) and n["fine"] > n["best"]


def test_args_matching_rules():
    assert args_ok({"query": "Kubernetes operators"}, {"query~": "kubernetes"})
    assert args_ok({"params": {"job_id": "J-1"}}, {"job_id": "j-1"})
    assert not args_ok({"job_id": "j-2"}, {"job_id": "j-1"})
    assert args_ok({"dry_run": True}, {"dry_run": "true"})


def test_oracle_scores_perfectly_on_every_arm():
    llm = LLMRouter(ScriptedBackend(oracle), load_config())
    res = asyncio.run(evaluate(list(ARMS), llm, "rdc_single", 0.0))
    for a in ARMS:
        assert res[a]["overall"]["tool_accuracy"] == 1.0 and res[a]["overall"]["arg_accuracy"] == 1.0, a

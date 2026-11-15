"""R&D-A (assignment): does tool granularity change agent behaviour? 40 tasks with known correct calls, 4 tool sets, ONE model, ONE step.

    uv run python -m evals.runners.rda_tools                    # real model (Groq key in .env); ~160 short calls
    uv run python -m evals.runners.rda_tools --offline          # plumbing only (oracle stand-in), never quote
    uv run python -m evals.runners.rda_tools --node analyst     # cheaper model if the provider quota is tight

Arms (same 40 tasks, same prompt, same model; only the tool list differs):
  fine    30 tools: the shipped tools with the four broadest ones split by facet (get_job -> 4, get_client_history -> 3, search_evidence -> 3,
          list_skills -> 2, get_profile -> 2) and terse descriptions
  coarse  4 tools: marketplace_read / marketplace_write / portfolio / workspace, each with an `action` parameter
  terse   the shipped 20 tools and parameters, descriptions reduced to the tool name (isolates the effect of descriptions)
  best    the shipped 20 tools with their real descriptions (read from tests/contract/snapshots/tool_schemas.json)
Metrics: tool_accuracy, arg_accuracy (right tool AND all checked args right), arg_given_tool, tokens_per_task (provider-reported in+out; includes the
tool list, so it measures what the tool surface costs), spec_tokens_est (len/4). Gold is known by construction. Limits: n = 40 (one task = 2.5 points),
the arms differ in granularity AND wording by design (the `terse` arm separates the two), one model. Never writes numbers that were not produced by a run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

from pydantic import BaseModel, Field

from agent.llm import LLMRouter, ScriptedBackend
from agent.settings import ROOT, load_config
from evals.runners.rag_eval import RESULTS

SNAPSHOT = ROOT / "tests" / "contract" / "snapshots" / "tool_schemas.json"
ARMS = ("fine", "coarse", "terse", "best")
GROUP = {"search_evidence": "portfolio", "get_case_study": "portfolio", "list_skills": "portfolio", "get_profile": "portfolio",
         "create_proposal_draft": "marketplace_write", "submit_proposal": "marketplace_write", "send_message": "marketplace_write",
         "get_free_slots": "workspace", "list_holds": "workspace", "hold_slot": "workspace", "release_hold": "workspace",
         "draft_email": "workspace", "list_email_drafts": "workspace"}  # every other shipped tool is marketplace_read

# (task, shipped tool, gold args ("key~" = argument must CONTAIN the value), fine-arm tool or None = same name)
TASKS: list[tuple[str, str, dict[str, Any], str | None]] = [
    ("Find open jobs about Kubernetes operators.", "search_jobs", {"query~": "kubernetes"}, None),
    ("Search for freelance gigs on data pipelines.", "search_jobs", {"query~": "pipeline"}, None),
    ("Look for jobs that mention LangGraph.", "search_jobs", {"query~": "langgraph"}, None),
    ("Read the description of job j-2041.", "get_job", {"job_id": "j-2041"}, "get_job_description"),
    ("What is the budget of job j-3310?", "get_job", {"job_id": "j-3310"}, "get_job_budget"),
    ("Which skills does job j-1187 require?", "get_job", {"job_id": "j-1187"}, "get_job_skills"),
    ("What screening questions does job j-5520 ask?", "get_job", {"job_id": "j-5520"}, "get_job_screening_questions"),
    ("What is the rating of client c-908?", "get_client_history", {"client_id": "c-908"}, "get_client_rating"),
    ("Is the payment method of client c-114 verified?", "get_client_history", {"client_id": "c-114"}, "get_client_payment_status"),
    ("How much has client c-642 spent in total?", "get_client_history", {"client_id": "c-642"}, "get_client_total_spent"),
    ("How many Connects do I have left?", "get_connects_balance", {}, None),
    ("What are the current platform rules and the Connects surcharge?", "get_platform_policy", {}, None),
    ("What is the status of proposal p-771?", "get_proposal", {"proposal_id": "p-771"}, None),
    ("Show the conversation on proposal p-205.", "list_messages", {"proposal_id": "p-205"}, None),
    ("Has proposal p-318 been replied to yet?", "get_proposal", {"proposal_id": "p-318"}, None),
    ("List the messages for proposal p-990.", "list_messages", {"proposal_id": "p-990"}, None),
    ("Find a code sample in my repos that shows FastAPI work.", "search_evidence", {"requirement~": "fastapi"}, "search_evidence_code"),
    ("Find a client review that praises my communication.", "search_evidence", {"requirement~": "communication"}, "search_evidence_reviews"),
    ("Find a case study showing RAG evaluation.", "search_evidence", {"requirement~": "rag"}, "search_evidence_case_studies"),
    ("Find code that shows Postgres migrations.", "search_evidence", {"requirement~": "postgres"}, "search_evidence_code"),
    ("Find a review that mentions on-time delivery.", "search_evidence", {"requirement~": "delivery"}, "search_evidence_reviews"),
    ("Find a case study about demand forecasting.", "search_evidence", {"requirement~": "forecast"}, "search_evidence_case_studies"),
    ("Open case study cs-07 in full.", "get_case_study", {"case_id": "cs-07"}, None),
    ("Show me case study cs-12 together with its reviews.", "get_case_study", {"case_id": "cs-12"}, None),
    ("Which of my declared skills have evidence behind them?", "list_skills", {}, "list_evidenced_skills"),
    ("Which declared skills have no evidence, so I must not claim them?", "list_skills", {}, "list_unevidenced_skills"),
    ("What is my hourly rate?", "get_profile", {}, "get_hourly_rate"),
    ("What is the minimum project budget I accept?", "get_profile", {}, "get_minimum_budget"),
    ("Which slots are free for a 30 minute call between 2026-11-23 and 2026-11-27?", "get_free_slots",
     {"date_from~": "2026-11-23", "date_to~": "2026-11-27", "duration_min": "30"}, None),
    ("Find free 60 minute slots between 2026-12-01 and 2026-12-03.", "get_free_slots",
     {"date_from~": "2026-12-01", "date_to~": "2026-12-03", "duration_min": "60"}, None),
    ("List my current tentative calendar holds.", "list_holds", {}, None),
    ("Tentatively hold 2026-11-24T10:00 for the interview on proposal p-771.", "hold_slot", {"start~": "2026-11-24t10:00", "proposal_id": "p-771"}, None),
    ("Release hold h-31.", "release_hold", {"hold_id": "h-31"}, None),
    ("Draft an email to anna@example.com with the subject 'Interview times' offering two slots.", "draft_email",
     {"to": "anna@example.com", "subject~": "interview"}, None),
    ("List the email drafts for proposal p-205.", "list_email_drafts", {"proposal_id": "p-205"}, None),
    ("I no longer need hold h-9, release it.", "release_hold", {"hold_id": "h-9"}, None),
    ("Save this text as a draft proposal for job j-2041: 'Hello, I can build your pipeline.'", "create_proposal_draft", {}, None),
    ("Preview the cost and policy checks of submitting proposal p-771 without sending it.", "submit_proposal",
     {"proposal_id": "p-771", "dry_run": "true"}, None),
    ("Do a dry run of submitting proposal p-205.", "submit_proposal", {"proposal_id": "p-205", "dry_run": "true"}, None),
    ("The human approved token tok-77. Send 'Thursday 10:00 works for me' on proposal p-318.", "send_message",
     {"proposal_id": "p-318", "text~": "thursday"}, None),
]

FINE_SPLIT: dict[str, list[tuple[str, str]]] = {  # shipped tool -> [(fine tool, params)]
    "get_job": [("get_job_description", "job_id*:string"), ("get_job_budget", "job_id*:string"), ("get_job_skills", "job_id*:string"),
                ("get_job_screening_questions", "job_id*:string")],
    "get_client_history": [("get_client_rating", "client_id*:string"), ("get_client_payment_status", "client_id*:string"),
                           ("get_client_total_spent", "client_id*:string")],
    "search_evidence": [("search_evidence_code", "requirement*:string"), ("search_evidence_case_studies", "requirement*:string"),
                        ("search_evidence_reviews", "requirement*:string")],
    "list_skills": [("list_evidenced_skills", ""), ("list_unevidenced_skills", "")],
    "get_profile": [("get_hourly_rate", ""), ("get_minimum_budget", "")],
}


class Call(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


def load_shipped() -> dict[str, dict[str, Any]]:
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return {name: spec for server in snap.values() for name, spec in server.items()}


def _type(p: dict[str, Any]) -> str:
    if "type" in p:
        return str(p["type"])
    return "|".join(str(x.get("type", "any")) for x in p.get("anyOf", [])) or "any"


def params_of(spec: dict[str, Any]) -> str:
    props, req = spec["input"].get("properties", {}), set(spec["input"].get("required", []))
    return ", ".join(f"{k}{'*' if k in req else ''}:{_type(v)}" for k, v in props.items())


def humanise(name: str) -> str:
    return name.replace("_", " ").capitalize() + "."


def build_tools(arm: str, shipped: dict[str, dict[str, Any]]) -> str:
    """Compact text rendering of one arm's tool list (this IS the tool surface the model sees)."""
    if arm == "best":
        return "\n".join(f"{n}({params_of(s)}): {' '.join((s['description'] or '').split())}" for n, s in shipped.items())
    if arm == "terse":
        return "\n".join(f"{n}({params_of(s)}): {humanise(n)}" for n, s in shipped.items())
    if arm == "fine":
        out = []
        for n, s in shipped.items():
            if n in FINE_SPLIT:
                out += [f"{fn}({p}): {humanise(fn)}" for fn, p in FINE_SPLIT[n]]
            else:
                out.append(f"{n}({params_of(s)}): {humanise(n)}")
        return "\n".join(out)
    acts = {g: sorted(n for n in shipped if (GROUP.get(n) or "marketplace_read") == g)
            for g in ("marketplace_read", "marketplace_write", "portfolio", "workspace")}
    return "\n".join(f"{g}(action*:string, params:object): Calls one {g.replace('_', ' ')} action. Actions: {', '.join(a)}. Put the action's own parameters in `params`."
                     for g, a in acts.items())


def gold_for(arm: str, tool: str, args: dict[str, Any], fine: str | None) -> tuple[str, dict[str, Any]]:
    if arm == "fine":
        return (fine or tool), args
    if arm == "coarse":
        return (GROUP.get(tool) or "marketplace_read"), {"action": tool, **args}
    return tool, args


def flatten(args: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in args.items() if k != "params"}
    if isinstance(args.get("params"), dict):
        out.update(args["params"])
    return out


def norm(v: Any) -> str:
    return str(v).strip().lower()


def args_ok(got: dict[str, Any], gold: dict[str, Any]) -> bool:
    got = {k: v for k, v in flatten(got).items()}
    for k, v in gold.items():
        if k.endswith("~"):
            if norm(v) not in norm(got.get(k[:-1], "")):
                return False
        elif norm(got.get(k, "")) != norm(v):
            return False
    return True


def score(arm: str, task: tuple[str, str, dict[str, Any], str | None], call: Call | None) -> dict[str, Any]:
    text, tool, args, fine = task
    gtool, gargs = gold_for(arm, tool, args, fine)
    tool_ok = call is not None and call.tool == gtool
    return {"task": text, "gold_tool": gtool, "got_tool": call.tool if call else None, "tool_ok": tool_ok,
            "args_ok": bool(tool_ok and call is not None and args_ok(call.args, gargs))}


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    ok_tool = [r for r in rows if r["tool_ok"]]
    return {"n": n, "tool_accuracy": round(sum(r["tool_ok"] for r in rows) / n, 4), "arg_accuracy": round(sum(r["args_ok"] for r in rows) / n, 4),
            "arg_given_tool": round(sum(r["args_ok"] for r in ok_tool) / len(ok_tool), 4) if ok_tool else None,
            "tokens_per_task": round(sum(r["tokens"] for r in rows) / n, 1), "errors": sum(1 for r in rows if r.get("error"))}


def oracle(nm, system: str, user: str) -> str:  # offline stand-in: answers with the gold call of the task in the prompt (plumbing only)
    task = user.split("<task>")[1].split("</task>")[0]
    arm_tools = user.split("<tools>")[1].split("</tools>")[0].strip()
    t = next(t for t in TASKS if t[0] == task)
    arm = "coarse" if arm_tools.startswith("marketplace_read(") else "fine" if "get_job_budget(" in arm_tools else "best"
    gtool, gargs = gold_for(arm, t[1], t[2], t[3])
    return json.dumps({"tool": gtool, "args": {k.rstrip("~"): v for k, v in gargs.items()}})


async def evaluate(archs: list[str], llm: LLMRouter, node: str, pace: float) -> dict[str, Any]:
    shipped = load_shipped()
    tools = {a: build_tools(a, shipped) for a in archs}
    rows: dict[str, list[dict[str, Any]]] = {a: [] for a in archs}
    streak = 0
    for i, task in enumerate(TASKS, 1):
        for a in archs:
            user = f"<tools>\n{tools[a]}\n</tools>\n<task>{task[0]}</task>"
            call, tokens, err = None, 0, None
            try:
                call, chat = await llm.complete_json(node, "rda_tool_choice", user, Call, max_tokens=200)
                tokens, streak = chat.tokens_in + chat.tokens_out, 0
            except Exception as e:  # counted as a wrong answer, never silently dropped
                err, streak = f"{type(e).__name__}: {str(e)[:160]}", streak + 1
            r = score(a, task, call) | {"arm": a, "tokens": tokens}
            if err:
                r["error"] = err
            rows[a].append(r)
            print(f"[{i}/{len(TASKS)}] {a:<6} tool_ok={r['tool_ok']} args_ok={r['args_ok']} {err or ''}", flush=True)
            if streak >= 5:
                raise SystemExit(f"STOP: 5 consecutive failed calls ({err}). Provider quota or key problem: wait, check GROQ_API_KEY, or use --node analyst.")
            if pace:
                await asyncio.sleep(pace)
    return {a: {"overall": summarise(rs) | {"spec_tokens_est": len(tools[a]) // 4}, "rows": rs} for a, rs in rows.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archs", nargs="*", default=list(ARMS), choices=ARMS)
    ap.add_argument("--node", default="rdc_single", help="config node whose model answers (default: the 120b writer-class model)")
    ap.add_argument("--pace", type=float, default=1.0, help="seconds between calls (free-tier rate limits)")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    cfg = load_config()
    llm = LLMRouter(ScriptedBackend(oracle), cfg) if a.offline else LLMRouter(cfg=cfg)
    t0 = time.time()
    res = asyncio.run(evaluate(a.archs, llm, a.node, 0.0 if a.offline else a.pace))
    stem = "rda_tool_granularity" + ("_OFFLINE_plumbing" if a.offline else "")
    nm = cfg["models"]["profiles"]["open"]["nodes"].get(a.node)
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": "offline-oracle-plumbing-only" if a.offline else "real-model", "node": a.node,
               "model": nm, "n_tasks": len(TASKS), "seconds": round(time.time() - t0), "results": {k: v["overall"] for k, v in res.items()},
               "rows": {k: v["rows"] for k, v in res.items()}}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    cols = ["tool_accuracy", "arg_accuracy", "arg_given_tool", "tokens_per_task", "spec_tokens_est", "errors"]
    lines = ["| tool set | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1)]
    lines += [f"| {k} | " + " | ".join(str(v["overall"][c]) for c in cols) + " |" for k, v in res.items()]
    note = "<!-- OFFLINE oracle: plumbing only, NOT a result -->\n" if a.offline else ""
    (RESULTS / f"{stem}_table.md").write_text(note + "\n".join(lines) + "\n", encoding="utf-8")
    print(note + "\n".join(lines))


if __name__ == "__main__":
    main()

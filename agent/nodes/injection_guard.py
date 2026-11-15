"""Injection guard node (R9): runs right after intake and BEFORE the parallel Analyst / Risk / Fit branches.

1. Normalises the untrusted job text (NFKC, invisible/bidi characters, look-alike letters) and writes the cleaned job back, so every later
   regex rule and prompt sees the text a human sees, not an obfuscated variant.
2. Scores the text with the structural heuristics (agent/security/detector.py) and, if one is attached, an external classifier
   (`Deps.injection_classifier`: async callable text -> score 0..1, e.g. open-weight Prompt Guard). The higher score wins.
3. Records the result in `state["injection"]`. It never skips a job and never calls an LLM: the Risk Assessor turns a `high` level into the
   `prompt_injection` flag (ignored, low weight), and the structural defences (tool-less analyst, approval gate) do the rest.
"""
from __future__ import annotations

import inspect
import time
from typing import Any

from agent.security.detector import detect, level_for, normalise

from .common import Deps, fail, finish, job_text


async def injection_guard(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        job = dict(state["job"])
        removed = 0
        for key in ("title", "description"):
            if isinstance(job.get(key), str):
                job[key], n = normalise(job[key])
                removed += n
        if isinstance(job.get("screening_questions"), list):
            qs = []
            for q in job["screening_questions"]:
                c, n = normalise(str(q))
                qs.append(c)
                removed += n
            job["screening_questions"] = qs
        icfg = (deps.cfg.get("security") or {}).get("injection", {})
        det = detect(job_text(job), icfg)
        det.removed_chars += removed  # detect() saw already-clean text; keep the count of what normalisation removed
        if removed:
            det.reasons.insert(0, f"{removed} invisible/control character(s) removed")
            det.score = min(1.0, det.score + 0.4)
        errs: list[str] = []
        if deps.injection_classifier is not None:
            try:
                raw = deps.injection_classifier(job_text(job))
                score = float(await raw if inspect.isawaitable(raw) else raw)
                if score > det.score:
                    det.score = score
                if score >= icfg.get("classifier_flag", 0.5):
                    det.reasons.append(f"classifier score {score:.2f}")
            except Exception as e:  # the classifier is an extra layer: its failure degrades, it never stops the run
                errs.append(f"injection_guard: classifier unavailable ({type(e).__name__})")
        det.level = level_for(det.score, icfg)
        upd: dict[str, Any] = {"injection": det.as_dict()}
        if job != state["job"]:
            upd["job"] = job
        if errs:
            upd["errors"] = errs
        return finish("injection_guard", t0, upd, mode="heuristic" + ("+classifier" if deps.injection_classifier else ""),
                      level=det.level, score=round(det.score, 3))
    except Exception as e:
        return fail("injection_guard", t0, e)

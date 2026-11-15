"""Prompt Guard classifier (5.8 layer three): tolerant parsing, segmentation, max-over-segments, failure behaviour, factory without a key.
Uses httpx.MockTransport: no network. The live response format is confirmed by scripts/smoke_prompt_guard.py (owner)."""
from __future__ import annotations

import httpx
import pytest

from agent.nodes.common import Deps
from agent.nodes.injection_guard import injection_guard
from agent.security.prompt_guard import PromptGuardClassifier, build_prompt_guard, parse_score, segments


def mock_client(answer_for):
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer k" and body["model"] == "m"
        return httpx.Response(200, json={"choices": [{"message": {"content": answer_for(body["messages"][0]["content"])}}]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_parse_score_is_tolerant_and_strict_about_garbage():
    assert parse_score("0.97") == 0.97 and parse_score(" 1 ") == 1.0 and parse_score("score: 0.02") == 0.02
    assert parse_score("1.7") == 1.0 and parse_score("MALICIOUS") == 1.0 and parse_score("BENIGN") == 0.0
    assert parse_score("0.00047248948249034584") == pytest.approx(0.00047, abs=1e-5)  # real Groq answer format (owner smoke run)
    assert parse_score("4.7e-05") == pytest.approx(4.7e-05) and parse_score("9.9E-01") == pytest.approx(0.99)  # not read as 5 / 1
    with pytest.raises(ValueError):
        parse_score("I cannot help with that")
    with pytest.raises(ValueError):
        parse_score("")


def test_segments_split_and_cap():
    assert segments("a" * 2500, 1000, 6) == ["a" * 1000, "a" * 1000, "a" * 500]
    assert len(segments("a" * 100000, 1000, 6)) == 6 and segments("", 1000, 6) == [""]


async def test_classifier_takes_the_max_over_segments_and_url_is_built():
    seen = []

    def answer(text):
        seen.append(len(text))
        return "0.95" if "EVIL" in text else "0.01"

    clf = PromptGuardClassifier("https://api.example/v1/", "k", "m", client=mock_client(answer), segment_chars=100)
    assert clf.url == "https://api.example/v1/chat/completions"
    assert await clf("x" * 100 + "EVIL" + "y" * 50) == 0.95 and len(seen) == 2
    assert await clf("all good here") == 0.01 and await clf("   ") == 0.0
    await clf.aclose()


async def test_http_failure_raises_and_the_guard_degrades():
    def handler(request):
        return httpx.Response(503, json={})

    clf = PromptGuardClassifier("https://api.example/v1", "k", "m", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await injection_guard({"job": {"job_id": "j", "title": "t", "description": "Need FastAPI help."}}, Deps(injection_classifier=clf))
    assert out["injection"]["level"] == "none" and out["errors"] == ["injection_guard: classifier unavailable (HTTPStatusError)"]


async def test_guard_uses_the_classifier_score():
    clf = PromptGuardClassifier("https://api.example/v1", "k", "m", client=mock_client(lambda t: "0.99"))
    out = await injection_guard({"job": {"job_id": "j", "title": "t", "description": "Need FastAPI help."}}, Deps(injection_classifier=clf))
    assert out["injection"]["level"] == "high" and out["events"][0]["mode"] == "heuristic+classifier"


def test_factory_returns_none_without_a_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert build_prompt_guard() is None
    monkeypatch.setenv("GROQ_API_KEY", "abc")
    clf = build_prompt_guard()
    assert clf is not None and clf.model == "meta-llama/llama-prompt-guard-2-86m" and clf.url.endswith("/chat/completions")

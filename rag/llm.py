"""Tiny OpenAI-compatible chat client for the RAG variants that can use an LLM (V1/V6/V7/V8 extras, claim judge).

Provider/model come from agent/config.yaml via agent.settings.node_model(node) (Ollama, Groq, OpenRouter all speak
this API). Prompts are files in agent/prompts/ (repo rule: no prompt text in Python). Everything here is optional:
variants have deterministic fallbacks and record which mode produced a result.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from agent.settings import node_model

PROMPTS = Path(__file__).resolve().parent.parent / "agent" / "prompts"


def load_prompt(name: str) -> tuple[str, str]:
    """Return (text, sha12). The hash is logged with every LLM call so traces name the exact prompt version."""
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    return text, hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


class LLM(Protocol):
    name: str
    calls: int

    def complete(self, system: str, user: str, *, max_tokens: int = 300) -> str: ...


class OpenAICompatLLM:
    def __init__(self, node: str = "analyst", timeout: float = 60.0):
        self.nm = node_model(node)
        self.name, self.timeout, self.calls = f"{self.nm.provider}/{self.nm.model}", timeout, 0

    def complete(self, system: str, user: str, *, max_tokens: int = 300) -> str:
        import httpx

        headers = {"Authorization": f"Bearer {self.nm.api_key}"} if self.nm.api_key else {}
        r = httpx.post(
            f"{self.nm.base_url.rstrip('/')}/chat/completions", headers=headers, timeout=self.timeout,
            json={"model": self.nm.model, "temperature": 0, "max_tokens": max_tokens,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        )
        r.raise_for_status()
        self.calls += 1
        return (r.json()["choices"][0]["message"]["content"] or "").strip()


class ScriptedLLM:
    """Deterministic stand-in for tests: fn(system, user) -> text."""

    def __init__(self, fn: Callable[[str, str], str], name: str = "scripted"):
        self.fn, self.name, self.calls = fn, name, 0

    def complete(self, system: str, user: str, *, max_tokens: int = 300) -> str:
        self.calls += 1
        return self.fn(system, user)

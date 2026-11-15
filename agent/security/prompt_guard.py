"""Layer three of the injection defence (R9, 5.8): the open-weight Llama Prompt Guard 2 (86M) classifier hosted on Groq.

Plugs into the guard node as `Deps.injection_classifier` (async callable: text -> score 0..1). Built from config
`security.injection_classifier` + the Groq provider of the active model profile; returns None when no API key exists, so the system
runs heuristics-only instead of failing.

Facts from Groq's model page (checked 2026-11-15): called through chat completions, output is plain text, context window 512 tokens
(longer inputs must be split into segments), and the output format is NOT documented. So the parser is deliberately tolerant
(first number in [0,1], or a benign/malicious label word) and `scripts/smoke_prompt_guard.py` prints real responses for the owner to
confirm. An unparseable answer raises: the guard node records the failure and degrades to heuristics; it never blocks on it.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import httpx

from agent.settings import load_config

_NUM = re.compile(r"(?<![\w.])[+-]?(?:\d+\.\d+|\d+|\.\d+)(?:[eE][+-]?\d+)?(?![\w.])")  # incl. scientific notation: 4.7e-05


def parse_score(content: str) -> float:
    """Model text -> injection score in [0, 1]. Raises ValueError if the answer has neither a number nor a known label."""
    text = (content or "").strip()
    m = _NUM.search(text)
    if m:
        return min(1.0, max(0.0, float(m.group(0))))
    low = text.lower()
    if any(w in low for w in ("injection", "jailbreak", "malicious", "unsafe")):
        return 1.0
    if any(w in low for w in ("benign", "safe")):
        return 0.0
    raise ValueError(f"cannot parse prompt-guard output: {text[:60]!r}")


def segments(text: str, size: int = 1200, limit: int = 6) -> list[str]:
    """Split long text into <= `limit` pieces of ~`size` characters (about 300 tokens, under the model's 512-token window)."""
    text = text or ""
    return [text[i:i + size] for i in range(0, len(text), size)][:limit] or [""]


class PromptGuardClassifier:
    def __init__(self, base_url: str, api_key: str, model: str, client: httpx.AsyncClient | None = None, timeout: float = 10.0,
                 segment_chars: int = 1200, max_segments: int = 6):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.segment_chars, self.max_segments = model, segment_chars, max_segments
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def _one(self, piece: str) -> float:
        r = await self._client.post(self.url, headers=self._headers,
                                    json={"model": self.model, "messages": [{"role": "user", "content": piece}]})
        r.raise_for_status()
        return parse_score(r.json()["choices"][0]["message"]["content"])

    async def __call__(self, text: str) -> float:
        """Highest score over all segments (one injected segment is enough)."""
        pieces = [p for p in segments(text, self.segment_chars, self.max_segments) if p.strip()]
        if not pieces:
            return 0.0
        return max(await asyncio.gather(*(self._one(p) for p in pieces)))

    async def aclose(self) -> None:
        await self._client.aclose()


def build_prompt_guard(cfg: dict[str, Any] | None = None) -> PromptGuardClassifier | None:
    """From config; None when the provider key is missing (heuristics-only mode). Callers load .env into the environment first."""
    cfg = cfg or load_config()
    spec = (cfg.get("security") or {}).get("injection_classifier")
    if not spec:
        return None
    prov = cfg["models"]["profiles"]["open"]["providers"].get(spec["provider"])
    key = os.environ.get(prov.get("api_key_env") or "") if prov else None
    return PromptGuardClassifier(prov["base_url"], key, spec["model"]) if prov and key else None

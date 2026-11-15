"""Model router for graph nodes: node -> (primary, fallback) from agent/config.yaml; OpenAI-compatible HTTP (Ollama, Groq,
OpenRouter, vLLM). Models and prompts are DATA: no model id or prompt text lives in node code.

Per call it returns a ChatResult with tokens, latency, prompt hash and whether the fallback answered, so nodes can write
`usage` and `events` into the graph state (cost/latency ledger, R8). `complete_json` validates against a Pydantic model and
makes ONE repair attempt, then raises StructuredOutputError (the graph turns that into needs_attention, never a guess).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .observability.tracing import set_attrs, span
from .settings import NodeModel, load_config, node_model

PROMPTS = Path(__file__).resolve().parent / "prompts"
T = TypeVar("T", bound=BaseModel)
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


class LLMUnavailable(RuntimeError):
    """Primary and fallback both failed."""


class StructuredOutputError(ValueError):
    """Model output could not be validated after one repair attempt."""


class ChatResult(BaseModel):
    text: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    prompt_sha: str = ""
    fallback_used: bool = False

    def usage(self, price_per_mtok: tuple[float, float] = (0.0, 0.0)) -> dict[str, float]:
        cost = (self.tokens_in * price_per_mtok[0] + self.tokens_out * price_per_mtok[1]) / 1_000_000
        return {"tokens_in": self.tokens_in, "tokens_out": self.tokens_out, "llm_calls": 1, "cost_usd": cost}


def load_prompt(name: str) -> tuple[str, str]:
    """(text, sha12). The hash is logged on every call so a trace names the exact prompt version (assignment C3)."""
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    return text, hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


class ChatBackend(Protocol):
    async def chat(self, nm: NodeModel, system: str, user: str, max_tokens: int, json_mode: bool) -> tuple[str, int, int]:
        """Return (text, tokens_in, tokens_out)."""
        ...


class HttpBackend:
    def __init__(self, timeout: float = 60.0, max_retries: int = 3, max_wait: float = 20.0):
        self.timeout = timeout
        self.max_retries = max_retries  # 429/5xx are retried with Retry-After backoff (free tiers rate-limit hard)
        self.max_wait = max_wait

    async def chat(self, nm: NodeModel, system: str, user: str, max_tokens: int, json_mode: bool) -> tuple[str, int, int]:
        import httpx

        headers = {"Authorization": f"Bearer {nm.api_key}"} if nm.api_key else {}
        body: dict[str, Any] = {
            "model": nm.model, "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        url = f"{nm.base_url.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            for attempt in range(self.max_retries + 1):
                r = await c.post(url, headers=headers, json=body)
                if r.status_code not in (429, 500, 502, 503, 504) or attempt == self.max_retries:
                    break
                try:
                    wait = float(r.headers.get("retry-after", ""))
                except ValueError:
                    wait = 2.0 * (2**attempt)
                if wait > self.max_wait:  # quota exhausted, not a blip: fail fast, let the fallback handle it
                    why = r.text[:160].replace("\n", " ")  # Groq names the limit hit (TPD/TPM/RPD) in the body
                    print(f"[llm] {nm.model}: HTTP {r.status_code}, Retry-After {wait:.0f}s > cap, giving up: {why}",
                          file=sys.stderr, flush=True)
                    break
                print(f"[llm] {nm.model}: HTTP {r.status_code}, waiting {wait:.0f}s ({attempt + 1}/{self.max_retries})",
                      file=sys.stderr, flush=True)
                await asyncio.sleep(max(wait, 0.5))
        r.raise_for_status()
        data = r.json()
        u = data.get("usage") or {}
        return (data["choices"][0]["message"]["content"] or ""), int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))


def resolve_target(spec: str, cfg: dict[str, Any] | None = None) -> NodeModel:
    """'ollama/qwen3:4b' or 'groq/openai/gpt-oss-20b' -> NodeModel (provider = text before the FIRST slash)."""
    from .settings import active_profile

    cfg = cfg or load_config()
    provider, _, model = spec.partition("/")
    prov = cfg["models"]["profiles"][active_profile(cfg)]["providers"][provider]
    base = prov.get("base_url") or os.environ.get(prov.get("base_url_env", ""), prov.get("default_base_url", ""))
    key_env = prov.get("api_key_env")
    return NodeModel(provider=provider, model=model, base_url=base, api_key=os.environ.get(key_env) if key_env else None)


def extract_json(text: str) -> Any:
    """First JSON object/array in `text` (models wrap JSON in prose or code fences). Raises ValueError if none parses."""
    text = _THINK.sub("", text).strip()
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = text.find(open_c), text.rfind(close_c)
        if 0 <= i < j:
            try:
                return json.loads(text[i : j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found in model output")


class LLMRouter:
    def __init__(self, backend: ChatBackend | None = None, cfg: dict[str, Any] | None = None):
        self.backend: ChatBackend = backend or HttpBackend()
        self.cfg = cfg

    def targets(self, node: str) -> list[NodeModel]:
        primary = node_model(node, self.cfg)
        out = [primary]
        if primary.fallback:
            out.append(resolve_target(primary.fallback, self.cfg))
        return out

    async def complete(self, node: str, prompt: str, user: str, *, max_tokens: int = 600, json_mode: bool = False) -> ChatResult:
        system, sha = load_prompt(prompt)
        last: Exception | None = None
        for n, nm in enumerate(self.targets(node)):
            t0 = time.perf_counter()
            try:
                with span("llm.chat", node=node, provider=nm.provider, model=nm.model, prompt_sha=sha, fallback=n > 0) as sp:
                    text, tin, tout = await self.backend.chat(nm, system, user, max_tokens, json_mode)
                    set_attrs(sp, tokens_in=tin, tokens_out=tout)
            except Exception as e:  # transport/HTTP errors: try the fallback model
                last = e
                continue
            return ChatResult(text=_THINK.sub("", text).strip(), provider=nm.provider, model=nm.model, tokens_in=tin,
                              tokens_out=tout, latency_ms=round((time.perf_counter() - t0) * 1000, 2), prompt_sha=sha,
                              fallback_used=n > 0)
        raise LLMUnavailable(f"{node}: all models failed ({last!r})")

    async def complete_json(self, node: str, prompt: str, user: str, model_cls: type[T], *,
                            max_tokens: int = 800) -> tuple[T, ChatResult]:
        res = await self.complete(node, prompt, user, max_tokens=max_tokens, json_mode=True)
        try:
            return model_cls.model_validate(extract_json(res.text)), res
        except (ValueError, ValidationError) as first:
            fix = (f"{user}\n\nYour previous answer was invalid ({str(first)[:300]}). "
                   "Reply again with ONLY valid JSON that matches the schema.")
            res2 = await self.complete(node, prompt, fix, max_tokens=max_tokens, json_mode=True)
            try:
                merged = res2.model_copy(update={"tokens_in": res.tokens_in + res2.tokens_in,
                                                 "tokens_out": res.tokens_out + res2.tokens_out})
                return model_cls.model_validate(extract_json(res2.text)), merged
            except (ValueError, ValidationError) as second:
                raise StructuredOutputError(f"{node}: invalid structured output after repair: {second}") from second


class ScriptedBackend:
    """Test double: fn(nm, system, user) -> text (or raises). Counts calls and records which model answered."""

    def __init__(self, fn):
        self.fn, self.calls, self.seen = fn, 0, []

    async def chat(self, nm: NodeModel, system: str, user: str, max_tokens: int, json_mode: bool) -> tuple[str, int, int]:
        self.calls += 1
        self.seen.append(f"{nm.provider}/{nm.model}")
        return self.fn(nm, system, user), 10, 5

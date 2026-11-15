"""Tracing (assignment 8.3): OpenTelemetry -> Arize Phoenix. Optional and fail-safe: no endpoint / packages missing -> everything is a no-op.

  * LangGraph runs (every graph node) are captured by the OpenInference LangChain instrumentor.
  * Model calls go through our own OpenAI-compatible router (agent/llm.py), so it opens an explicit `llm.chat` span with node, model,
    prompt hash, tokens and fallback flag.
  * Every span carries `gigbid.trace_id`, the id that flows UI -> API -> graph -> MCP -> audit log, so one id finds a run everywhere.
Enabled when PHOENIX_COLLECTOR_ENDPOINT is set (compose sets it); TRACING=off disables it. Needs `uv sync --extra obs`.
"""
from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Iterator
from typing import Any

from agent.observability.context import get_trace_id

log = logging.getLogger("gigbid.tracing")
_tracer: Any = None


def tracing_enabled() -> bool:
    return _tracer is not None


def setup_tracing() -> bool:
    """Idempotent. Returns True when spans are exported."""
    global _tracer
    if _tracer is not None:
        return True
    endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "").strip()
    if not endpoint or os.environ.get("TRACING", "on").lower() == "off":
        return False
    try:
        from opentelemetry import trace
        from phoenix.otel import register

        provider = register(project_name=os.environ.get("PHOENIX_PROJECT_NAME", "gigbid"), endpoint=endpoint.rstrip("/") + "/v1/traces",
                            protocol="http/protobuf", batch=True, auto_instrument=False, verbose=False)
        try:
            from openinference.instrumentation.langchain import LangChainInstrumentor

            LangChainInstrumentor().instrument(tracer_provider=provider)
        except Exception as e:  # graph-node spans are a bonus; LLM spans below still work
            log.warning("LangChain instrumentation unavailable: %s", type(e).__name__)
        _tracer = trace.get_tracer("gigbid")
        log.info("tracing enabled -> %s", endpoint)
        return True
    except Exception as e:
        log.warning("tracing disabled (%s: %s)", type(e).__name__, e)
        return False


@contextlib.contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    """Span around a block; yields None when tracing is off. Exceptions are recorded and re-raised."""
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as sp:
        sp.set_attribute("gigbid.trace_id", get_trace_id())
        set_attrs(sp, **attrs)
        yield sp


def set_attrs(sp: Any, **attrs: Any) -> None:
    if sp is None:
        return
    for k, v in attrs.items():
        if v is not None:
            sp.set_attribute(f"gigbid.{k}", v if isinstance(v, (bool, int, float, str)) else str(v))

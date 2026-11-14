# ADR 0002: Infra choices (pgvector, Arq, Phoenix, OpenAI-compatible LLM client)

Status: accepted (2026-11-14)

- Vector store: Postgres + pgvector. One database for app data, LangGraph checkpoints and
  vectors means fewer services on a free host. BM25 via rank_bm25 in-process (small corpora).
- Worker: Arq on Redis. asyncio-native like LangGraph, tiny, idempotent job ids.
- Tracing: Arize Phoenix (OpenTelemetry/OpenInference), single container. Langfuse v3 needs
  ClickHouse and more RAM than free hosts offer.
- LLMs: `langchain-openai` against OpenAI-compatible endpoints so Ollama, Groq, OpenRouter and
  vLLM share one code path; model per node comes from `agent/config.yaml`.
- `mcp` pinned `<2` because `langchain-mcp-adapters` 0.3 resolves to mcp 1.x.

Trade-offs: pgvector is slower than a dedicated store at scale (irrelevant at ~10^4 chunks).

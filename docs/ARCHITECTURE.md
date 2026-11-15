# Architecture

Status: Phases 0-6 built (data layer, GigSim stand-in + marketplace port, 3 MCP servers + gateway, RAG, both graphs, memory,
API + worker). Web UI is Phase 7, compose/CI is Phase 8 (see GIGBID_PLAN.txt; live state in GIGBID_STATE.txt).

## Layers (implemented)
- **Marketplace port** (`agent/marketplace_port.py`): one typed interface, adapters for GigSim and live Upwork (R1).
- **MCP servers** (`mcp_servers/{portfolio,marketplace,workspace}`) + agent-side gateway (`agent/mcp_client.py`) with
  per-node allowlists from config. Write tools need an HMAC approval token (`agent/approvals.py`). See docs/TOOLS.md.
- **Policy as data** (`agent/policy/rules.yaml`), models/budgets/thresholds in `agent/config.yaml` (change-request ready).

## RAG (Part B), `rag/`
```
persona folder --loaders--> Corpus(items + metadata + proposal links + outcome signal)
                               |--units_fixed / units_multivector / contextual--> UnitIndex (BM25 + dense, in-memory)
JobQuery --> ComposedRetriever(flags) : decompose? hyde? -> dense | hybrid(RRF) -> rerank? -> outcome boost? -> diverse merge
         --> corrective loop? (grade per requirement, re-query exact term, abstain) --> Retrieval(ids, projects, abstained)
draft + evidence --> rag.verify: split -> label supported/partly/unsupported -> remove/rewrite -> before/after rates
```
- Variants V0..V8 are flag presets of one pipeline (`rag/variants/`); `rag.registry` builds them and the leave-one-out ablations.
- Evidence the writer may cite: case_study, readme, code, cv_bullet, review. Past proposals are signal only (outcome boost), never proof.
- Answer-key leakage rule: `gold_skills` and fabrication labels are never read by ingestion or retrieval.
- Embedders: `hashing` (offline stand-in, lexical-ish), `ollama` (nomic-embed-text), `st` (BGE-M3). Rerankers: `lexical`
  (stand-in) or `cross_encoder` (bge-reranker-v2-m3). Chosen in `agent/config.yaml: rag`. The pgvector backend (Phase 6)
  implements the same dense/bm25/rank methods as `rag.index.UnitIndex`.
- Evals: `evals/runners/{rag_eval,ablation,verify_eval}.py` -> `evals/results/`. Hypotheses: docs/RD_B_hypothesis.md.

## Agents (Part C), `agent/`
- `graphs/bid_graph.py`: intake -> injection_guard -> parallel Analyst / Risk / Fit (`Send`) -> join -> decision -> pricing -> writer
  -> verifier -> critic (max 2 loops) -> compliance -> `approval_gate` (LangGraph `interrupt`) -> `executor`. Budget stops end in
  `dead_letter` (needs attention), never silently. `graphs/conversation_graph.py`: load_context -> client_liaison -> `message_gate`
  -> `message_sender`. Diagrams are generated from the compiled graphs: `docs/images/*.mmd` (`make graphs`).
- Writes happen only in the deterministic `executor` / `message_sender`, only after a human decision, with idempotency keys derived
  from the exact content, so a crash/resume or retry never double-submits.
- Long-term memory (`agent/memory/`) is written by the API after a run, never from inside a graph; promotion rules are explicit.
- Injection defence is layered: normalisation + heuristics (`security/detector.py`), Prompt Guard classifier, tool-less Analyst,
  per-node tool allowlists.

## Product path for marketplace access (R1, R2, R4)
```
graph node / API / worker --MarketplacePort--> McpMarketplace --McpGateway(per-node allowlist, retries, circuit breaker)-->
  Marketplace MCP server (bearer auth, rate limit, audit log, approval-token check) --> GigSimAdapter | UpworkLive
```
- Config `marketplace.access: mcp|direct` (env `MARKETPLACE_ACCESS`); `direct` is for local experiments only.
- Approval authority: `POST /runs/{id}/decision` is the single approval point. It creates an in-process `ApprovalGrant`; the executor
  mints an HMAC token bound to the proposal (or to the exact message text). There is no endpoint that issues tokens. No grant (not
  yet decided, rejected, dismissed) means no token, and the MCP server refuses the write: it fails closed.
- Marketplace down -> typed `Unavailable`, API answers 503 `marketplace_unavailable` (UI banner), the worker retries later.

## API and worker (Part D backend, E1), `apps/api/`, `worker/`
- `main.py` (app factory, edge middleware: trace id + security headers), `container.py` (owns DB, checkpointer, memory, marketplace,
  models, event broker), `service.py` (`RunService`: start / decide / retry / recover), `streaming.py` (SSE bridge),
  `auth/` (scrypt + JWT cookie, login throttle, Fernet-encrypted OAuth token store), `schemas/` (the OpenAPI contract).
- Routers: `auth`, `jobs`, `proposals`, `messages`, `approvals` (pending queue), `runs` (start, trace, SSE, decision, retry), `memory`
  (view / delete / forget), `analytics`, `system` (health, personas, settings). All queries are scoped by `owner_id`; another user's
  object is a 404.
- A run is one LangGraph thread. The graph checkpoint is the source of truth; `run_events` is the replayable trace (SSE reads it, so
  `Last-Event-ID` reconnects miss nothing). Exactly one decision per approval (compare-and-set on status).
- Schema: Alembic (`apps/api/migrations`, `make migrate`); a test fails when models and migrations drift. `create_all` is the dev/test
  bootstrap only.
- Contract: `make openapi` writes `apps/api/openapi.json` (`--check` in CI); `make ts-client` generates the TypeScript client.
- Worker (`worker/`, Arq on Redis): scheduled scout (starts runs that stop at the human gate), outcome sync, stale-run recovery.
  All idempotent; cadence from `config.yaml: worker`.

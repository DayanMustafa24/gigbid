# Runbook

How to start, operate, deploy and recover GigBid. Commands are for the repo root.

## 1. Services

| Service | Port | Purpose | Image/command |
|---|---|---|---|
| web | 3000 | Next.js UI, proxies `/api` to the API (same origin) | `infra/docker/web.Dockerfile` |
| api | 8000 | FastAPI, SSE, auth, runs; applies Alembic migrations on start | python image |
| worker | - | Arq: scheduled scout, outcome sync, stale-run recovery | python image |
| portfolio-mcp / marketplace-mcp / workspace-mcp | 9101 / 9102 / 9103 | own MCP servers (bearer auth) | python image |
| gigsim | 8081 | marketplace simulator (`MARKETPLACE_MODE=gigsim`) | python image |
| postgres (pgvector) | 5432 | app DB + graph checkpoints | `pgvector/pgvector:pg17` |
| redis | 6379 | Arq queue | `redis:7-alpine` |
| phoenix | 6006 | trace UI (OpenTelemetry) | `arizephoenix/phoenix` |
| ollama (optional) | 11434 | local models, `--profile ollama` | `ollama/ollama` |

## 2. Start

Local, no Docker (SQLite + in-process checkpointer):
```
uv sync --extra dev
powershell -File scripts\dev_stack.ps1   # GigSim + 3 MCP servers (needs MCP_BEARER_TOKEN in .env)
uv run uvicorn apps.api.main:app --port 8000
cd apps/web && npm ci && npm run dev
```
Full stack (Docker):
```
cp .env.example .env     # fill the keys below
make preflight           # fails on weak/missing secrets, prints no secret values
make up                  # = docker compose up --build -d --wait
```
Required `.env` keys: `APP_SECRET_KEY` (>=24 random chars), `TOKEN_ENCRYPTION_KEY` (Fernet), `MCP_BEARER_TOKEN` (>=16 random),
`REVIEWER_EMAIL` + `REVIEWER_PASSWORD` (>=10 chars, seeded login). `GROQ_API_KEY` enables LLM drafting; without it nodes run in deterministic template mode.
Public HTTPS: set `COOKIE_SECURE=true` and a non-default `POSTGRES_PASSWORD`.

## 3. Health and degraded mode

* `GET /health` liveness. `GET /health/ready` reports marketplace, models, injection classifier, checkpointer kind.
* The UI shows a degraded banner from `/health/ready`. Marketplace down: reads fail with 503, runs resume later (checkpoints). Models down: nodes fall back
  to the configured fallback model, then to deterministic templates; the human still approves every draft.
* MCP gateway: circuit breaker per server (`mcp.cooldown_s`), retries honour `Retry-After`.
* Nothing is ever sent without a human approval; a missing approval grant fails closed at the Marketplace MCP server.

## 4. Rate limits

* Marketplace client: `marketplace.max_requests_per_minute` (20). Marketplace MCP server: 120 reads and 10 writes per minute per caller.
* Model providers: Groq free-tier limits apply; each node has a fallback model; per-job budgets in `budgets:` (steps, tokens) dead-letter a job instead of looping.

## 5. Scout interval (justification)

`worker.scout_every_minutes: 240` (4 h) and `marketplace.live_scout_interval_minutes: 240` for live mode.
* A freelancer needs to be early, not instant: replies to proposals are measured in hours, so a 4 h pass loses little against a 15 min pass.
* Politeness and account safety: a live marketplace may rate-limit or flag automated polling; 6 passes/day with `scout_limit_per_query: 10` is far below any plausible limit.
* Cost: each new bid run spends model calls and Connects budget; `max_runs_per_scout: 5` caps that per user per pass.
* GigSim has no such limits, so for demos set `worker.scout_every_minutes` lower in `agent/config.yaml`. The value is data, not code.

## 6. Recovery

* Crashed API/worker: runs are checkpointed (Postgres); the worker's recover job (every 15 min) marks runs of a dead process failed; `POST /runs/{id}/retry` resumes them.
  Writes reuse their idempotency keys, so a retry never double-submits.
* Migrations: `make migrate` (Alembic). Roll back one step: `uv run python -m apps.api.migrate downgrade`.
* Reset a local stack: `make down` then `docker compose -f infra/docker-compose.yml down -v` (deletes DB, memory and audit volumes).
* Forget a user: Settings -> Memory -> Delete all, or the API forget endpoint; deleted memory never comes back.
* Audit log (hash-chained) is in the `audit` volume: `/app/data/audit/marketplace_audit.jsonl`.

## 7. Checks

| Command | What |
|---|---|
| `make lint test` | ruff + pytest |
| `make openapi` / `export_openapi --check` | API contract committed and current |
| `make repo-rules` | no secrets, prompts only in `agent/prompts`, cited results exist, gigsim unchanged |
| `make pg-smoke` | migrations + Postgres checkpointer (needs Postgres) |
| `make ci-smoke` | 30-job gate from `ci_gates` (deterministic without `GROQ_API_KEY`, labelled) |
| `make smoke-live` | Playwright against the running compose stack |

CI (`.github/workflows/ci.yml`) runs all of them, including building and starting the whole compose stack on a GitHub runner.
`eval.yml` runs the 30-job gate on pull requests that touch agent/rag/evals and weekly.

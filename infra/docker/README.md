# infra/docker

| file | image | used by |
|---|---|---|
| `python.Dockerfile` | python 3.12 + uv-locked deps (+ `obs` extra) | api, worker, gigsim, portfolio-mcp, marketplace-mcp, workspace-mcp (same code, different command) |
| `web.Dockerfile` | Next.js standalone build (node 22) | web |
| `postgres-init.sql` | enables `pgvector` on first start | postgres |

Build context is always the **repo root**. Start everything: `make up` (see docs/RUNBOOK.md).

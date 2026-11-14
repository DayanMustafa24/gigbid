# gigsim_local: GigSim stand-in (NOT the instructor's simulator)

The starter kit did not ship `gigsim/`, so this is our own deterministic marketplace simulator. When the official
`gigsim/` arrives, drop it into `gigsim/` untouched; only `mcp_servers/marketplace/adapters/gigsim.py` may need changes.
Results measured here must be labelled "local stand-in" (see ADR 0001).

Run: `uv run python -m gigsim_local` -> http://127.0.0.1:8081/docs

| Env | Default | Meaning |
|---|---|---|
| GIGSIM_API_TOKEN | empty (auth off) | bearer token; set the same value in `.env` for the adapter |
| GIGSIM_DATASET | stream | `stream` (1,500 jobs), `dev` (600 jobs, gold stripped), `both`, `sample` (6 jobs, no seed needed) |
| GIGSIM_PERSONA | all | restrict the job feed to `ai_ml`, `fullstack_web` or `data_analyst` |
| GIGSIM_FAULT_RATE / GIGSIM_FAULT_AFTER_COMMIT_RATE | 0.02 / 0.01 | random 5xx before the action / after a write committed (tests idempotency) |
| GIGSIM_RATE_LIMIT_PER_MIN | 60 | 429 + Retry-After above this |
| GIGSIM_POLICY_UPDATE | off | `1` activates the stand-in policy update (`policy_update.yaml`) |

Surface: `/v1/jobs`, `/v1/jobs/{id}`, `/v1/clients/{id}/history`, `/v1/connects/balance`, `/v1/platform/policy`,
`/v1/proposals` (+ `/{id}`, `/{id}/submit?dry_run=`), `/v1/messages`. Writes require an `Idempotency-Key` header.
Admin (evals/demos only, never in the port): `/admin/reset`, `/admin/tick`, `/admin/policy-update`, `/admin/faults`,
`/admin/outcomes` (reply/hire rate + hidden-preference factors).

Time is manual: `POST /admin/tick` releases new jobs from the stream and reveals client decisions. Clients have hidden
preferences (must-have skills, budget ceiling, dislike of generic openings, want for evidence); the decision model is
rule-based and seeded (`client_model.py`), the one seam where an LLM-backed client can be swapped in. Scam clients answer
with off-platform lures; ~5% of normal client replies carry a planted prompt injection (R9 tests).
Gold labels from `evals/datasets` are never loaded into the simulator.

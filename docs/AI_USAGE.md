# AI Usage Disclosure

**Tool:** Claude (Anthropic) as the coding assistant across all phases, working in this repository through a filesystem connection. The assistant
could not run code on my machine: I ran every test, linter, build and evaluation and pasted failures back; only results from real runs were
recorded (rule: a cell stays empty until a results file exists). I remain responsible for all code and can explain it (see `docs/VIVA_PREP.md`).

**What the assistant did:** planning documents, data generators, the simulator stand-in, the three MCP servers and gateway, RAG variants and
verification, LangGraph graphs, API, worker, web UI, Docker/CI, tests, docs. **What I did:** decisions (stack, X2 extension, stand-ins for missing
instructor assets), running everything, keys and accounts, deployment, final review.

## Incorrect AI suggestions caught by tests or runs (and fixed)
Each of these was found by a failing test, a crash or reading real output, not assumed. (Owner: keep the ones you personally observed, add your own.)
1. **Verification metric that looked perfect.** `unsupported_after = 0` was reported as a result, but it is zero by construction after
   removal. Caught in review; the report now quotes `fabricated_per_proposal_after`, fabrication recall and the share of truthful claims kept.
2. **Evidence-pool mismatch in the verify evaluation.** The first run used a pool different from the live verifier's and showed precision 0.07 to
   0.17; the pool was aligned (retrieved items plus primary siblings of their projects) and the run repeated.
3. **Crash in the verify evaluation** (`ctx.source` should have been `ctx.corpus.source`), found on the first run.
4. **Prompt Guard output parser.** The model's output format is undocumented; a scientific-notation score (`4.7e-05`) was read as 5 and
   became 1.0. Found by the live smoke run; parser fixed and tested.
5. **Health endpoint lied about its configuration.** `/health/ready` and models showed false because `.env` was read only into pydantic settings,
   while model routing read `os.environ`; fixed with an explicit `load_env()` at startup.
6. **Postgres driver.** The compose setup first told the API to use `asyncpg`, which is not a dependency; switched to `psycopg` 3 (already
   installed, async-capable) and corrected the docs.
7. **Checkpointer kind reported from config only**, ignoring the `CHECKPOINTER` environment variable, so compose would have reported "sqlite".
8. **Repo rule check** flagged a deliberately fake key in a test and a filename pattern in a doc; both rewritten.

## Policy-update change request
Costs and rules live in `agent/policy/rules.yaml`, `agent/config.yaml` and `gigsim_local/policy_update.yaml`. The official text was not available;
the stand-in (+2 Connects, AI disclosure required) is labelled as invented. Record of files/lines/time for the real update: see `docs/RUNBOOK.md`
once applied.

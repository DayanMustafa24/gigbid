# Viva prep (what you must be able to explain, with the file to open)

| Question | Answer in one breath | Open |
|---|---|---|
| Why a hand-written graph, not a prompt-driven agent? | Order of safety-critical steps (verify, critic, compliance, human gate) must be guaranteed by code; R&D-C measures the alternatives. | `agent/graphs/bid_graph.py`, `evals/runners/rdc_architectures.py` |
| Where is the parallelism? | `Send` fan-out of Analyst, Risk, Fit after the injection guard, joined before the decision. | `bid_graph.py` |
| How does human approval work? | `interrupt()` before the executor; the decision endpoint is the only place that mints an approval grant; the Marketplace MCP server verifies an HMAC token bound to action + subject (+ text hash) and fails closed. | `agent/approvals.py`, `apps/api/service.py`, `mcp_servers/marketplace/server.py` |
| How are fabricated claims stopped? | Split into claims, label each against evidence, rewrite/remove unsupported ones, measure before/after. | `rag/verify.py`, `evals/results/rag_verify_eval.json` |
| Why is `unsupported_after = 0` not a result? | It is zero by construction after removal; the honest metrics are fabrication recall and truthful claims kept. | `docs/RD_B_results.md` |
| How did you choose the RAG design? | Ablation (leave-one-out of the best flags) on dev sets; reranker off because it hurt with the lexical stand-in; provisional until a real-embedder run. | `docs/RD_B_results.md` |
| Injection defence? | Quarantine tags, normalisation, heuristics, Prompt Guard score, tool-less analyst, allowlists per node, approval gate. Layers are complementary (one sample scored 0.006 by Prompt Guard but caught by heuristics). | `agent/security/`, `agent/config.yaml` allowlists |
| What happens on a crash mid-run? | Postgres checkpointer; replay reuses the same idempotency keys, so no double submit. | `agent/checkpointing.py`, `tests/integration/test_checkpointing.py` |
| How is the policy change absorbed? | Rules and costs are data (`rules.yaml`, `config.yaml`, `policy_update.yaml`); no code change for a new rule or cost. | `agent/policy/` |
| What is X2 and what are its limits? | Hash-assigned opener arms, z-test on reply rate; simulator client is a rule-based stand-in, so findings are about the simulator. | `docs/EXTENSION.md` |
| What is not verified? | Live Upwork mode, real-embedder RAG run, any metric without a results file. Say so. | `GIGBID_STATE.txt` |

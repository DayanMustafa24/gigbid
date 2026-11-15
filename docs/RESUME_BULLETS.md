# Resume bullets (numbers only from results files; synthetic data, say so if asked)

* Built GigBid, a multi-agent freelancer copilot (LangGraph, 3 custom MCP servers, advanced RAG, Next.js, Postgres) where a human approves every proposal and message; on 30 held-out dev jobs
  with open-weight models it made 0 scam bids, scored bid/skip F1 0.95 and p95 latency 13.5 s per job, with claim verification leaving 0 unsupported claims in final drafts (0.05 per draft before).
* Raised evidence retrieval recall@5 from 0.44 to 0.66 (and project recall 0.46 to 0.69) through measured ablation, and designed a proposal A/B framework whose z-test correctly reported an underpowered result (p = 0.34, n = 56 per arm) instead of a false win.

Sources: ci_smoke_table.md, rag_eval_table.md, rag_verify_eval.json, x2_experiment_table.md.

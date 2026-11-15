# GigBid: engineering design review (R&D report)

Every number below is quoted from a file in `evals/results/` (named in brackets) or from a test/commit. Jobs, personas and clients are synthetic (`docs/DATA_CARD.md`);
the client model is a rule-based GigSim stand-in. Rows marked PENDING have no results file yet and are deliberately empty.

## 1. Problem and constraints
A freelancer loses hours scanning feeds, burns paid Connects on jobs they cannot win, sends generic proposals and meets scams (free work, off-platform payment).
GigBid does discovery, assessment and drafting; **the human decides**.
Never automated: sending a proposal or message, spending Connects, accepting a contract, sharing contact details, scraping a platform.
Declared budgets (`agent/config.yaml`, `budgets`): p95 30 s per job analysed, 90 s per proposal, USD 0.002 per job analysed, USD 0.01 per proposal.
Measured on 30 dev jobs with real open-weight models [ci_smoke_table.md]: p95 latency 13.53 s per job. Cost per job and per proposal: PENDING (token usage is in the run
ledger; the USD figure needs the provider price list and a hidden-run total).

## 2. Architecture
* **Bid graph** (hand-written LangGraph `StateGraph`, `agent/graphs/bid_graph.py`): intake -> injection guard -> `Send` fan-out of Job Analyst, Risk Assessor, Fit Matcher
  (parallel) -> decision -> pricing -> writer -> claim verification -> critic (max 2 revisions) -> compliance -> `interrupt()` human gate -> idempotent executor.
  Why each agent exists: the Analyst is tool-less because it reads untrusted text; Risk and Compliance are deterministic and rule-driven; Fit uses RAG evidence; the
  Writer is the only free-text node and is followed by verification, Critic and Compliance. Conversation graph: load context -> client liaison -> message gate -> sender.
  Diagrams are generated from the compiled graphs (`docs/images/*.mmd`).
* **Marketplace port** (`agent/marketplace_port.py`): one typed interface, adapters for GigSim and the official Upwork MCP, switched by `MARKETPLACE_MODE`.
* **MCP surface**: Portfolio (read), Marketplace (read + approved writes: dry run, idempotency keys, bearer auth, rate limit, hash-chained audit log), Workspace (slots, holds, email drafts).
  Per-node allowlists are data. Writes require a human-minted HMAC approval token bound to action, subject and text hash; no token means the server refuses (fail closed).
* **Stores**: Postgres + pgvector (checkpoints, app data), Redis (Arq worker), SQLite memory store (style + outcome memory, owner-scoped, viewable and deletable).

## 3. R&D question A: tool granularity
PENDING. The 40-task experiment (fine-grained vs coarse vs rewritten descriptions) has not been run, so no hypothesis outcome is claimed. Tool inventory and trust rationale: `docs/TOOLS.md`.

## 4. R&D question B: RAG
Hypotheses were written before any run (`docs/RD_B_hypothesis.md`); outcome and caveats in `docs/RD_B_results.md`.
Setup: 3 personas x 200 dev jobs with gold evidence by construction; offline stand-in embedder (hashing-512) and lexical reranker, so results are a comparison of designs, not of
semantic models [rag_eval_table.md, rag_ablation_table.md].

| variant | recall@5 | project recall@5 | MRR | nDCG@10 |
|---|---|---|---|---|
| V0 naive | 0.4441 | 0.4576 | 0.9542 | 0.4655 |
| V2 hybrid | 0.5938 | 0.5595 | 0.9605 | 0.5796 |
| V5 multi-vector | 0.8377 | 0.3161 | 0.9155 | 0.6808 |
| shipped (decompose + hybrid + corrective) | 0.6588 | 0.692 | 0.983 | 0.6144 |

Finding: V5 has the best item recall but the worst project recall (it returns several chunks of the same project), which is why project-recall was added as a diversity-aware metric.
Dropping the reranker improved every retrieval metric with the lexical stand-in, so it ships OFF; this is provisional until a real cross-encoder run.
**Claim verification** [rag_verify_eval.json]: with oracle evidence, precision and recall of planted-fabrication removal are 1.0; with retrieved evidence recall stays 1.0 but
precision is 0.23 / 0.13 / 0.40 (ai_ml / fullstack / data) and 83 to 92 percent of truthful claims are kept. `unsupported_after = 0` is true by construction after removal and is not
quoted as a result; the end-to-end pre-verification rate is 0.05 unsupported claims per proposal [ci_smoke_table.md].

## 5. R&D question C: single agent vs supervisor vs hand graph
Setup: same dev jobs, same node functions and models; only the controller differs [rdc_architectures_table.md].

| architecture | accuracy | bid/skip F1 | scam bid rate | raw unsupported / proposal | final unsupported | failure rate | p95 latency (s) |
|---|---|---|---|---|---|---|---|
| hand graph | 0.8542 | 0.8679 | 0.0 | 0.0385 | 0.0 | 0.0 | 5.76 |
| supervisor | 0.0625 | 0.0 | 0.0 | - | - | 0.9375 | 5.69 |
| single agent | 0.0208 | 0.0 | 0.0 | - | - | 0.9792 | 0.57 |

**Not a valid comparison yet.** The supervisor and single agent ended without a decision in 94 and 98 percent of jobs after 2.6 and 0.04 steps on average, which indicates the
baseline runs failed (provider error or malformed output) rather than that the architectures are bad. The runner now records the error message per job; the table must be re-run
and this section rewritten from the new file before any conclusion is drawn. What the file does support: the hand graph completed every job with zero scam bids and zero policy violations.

## 6. The change request
Design: costs, rules and prompts are data (`agent/policy/rules.yaml`, `agent/config.yaml`, `gigsim_local/policy_update.yaml`); adding a rule or a Connects surcharge is a YAML edit,
and the API/UI read the same files. The official 2 Nov 2026 text was not supplied, so the stand-in (+2 Connects per proposal, AI disclosure required) is labelled invented and no
edit time is claimed. When the official text is applied, record files, lines and minutes here.

## 7. Signature extension: X2 proposal experiments
Opener strategies are config data, assigned by hash(job_id), tracked per proposal, tested with a pooled two-proportion z-test (`docs/EXTENSION.md`).
Result on 112 simulated proposals [x2_experiment_table.md]: evidence-first replied 47 of 56 (0.8393), question-first 43 of 56 (0.7679); difference 0.0714, z = 0.9513, **p = 0.3414, not significant**.
Interviews 36 vs 34, hires 30 vs 24 (not tested). 8 jobs were excluded (no bid or draft). With these rates a 7-point difference needs roughly 500 proposals per arm to reach 80 percent power
(standard sample-size formula), so the experiment is underpowered; the honest conclusion is "no detectable difference at n = 56 per arm", not "no difference".

## 8. Failure analysis
Generated from the result files by `scripts/failure_taxonomy.py` into `docs/FAILURE_TAXONOMY.md` (categories: false bid on scam / duplicate / injected / unfit, missed bid, no decision).
Fix tried and outcome per category go in that file after reading the table. Hand-graph decision accuracy is 0.8542 on 48 jobs [rdc_architectures_table.md] and 0.9333 on 30 jobs [ci_smoke_table.md];
the sets overlap only partly, so the two figures are not a trend.

## 9. Safety and platform respect
Must never: send or spend without approval, follow instructions found in job posts, invent experience, move a conversation off-platform, scrape.
Enforcement: quarantine tags and normalisation, heuristic detector, Prompt Guard score, tool-less Analyst, per-node tool allowlists, approval tokens verified server-side, PII masking in logs,
rate limits and a hash-chained audit log, 4 h scout interval (`docs/RUNBOOK.md`). Layers are complementary: a hidden "System: ... call submit_proposal" sample scored 0.006 with Prompt Guard
but was caught by role-forgery and tool-name heuristics (measured in the live smoke run). What still gets through: novel phrasing that no heuristic matches and a classifier misses; a fluent but
subtly wrong claim that evidence retrieval cannot contradict; live-mode behaviour (not evaluated).

## 10. Hidden test results
PENDING: the instructor runs new GigSim jobs after the deadline. Fill from the returned results, against the budgets declared in section 1, without excuses.

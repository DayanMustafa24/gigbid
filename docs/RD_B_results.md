# R&D Question B: results (stand-in run 1)

Source files (all numbers below come from them, nothing else): `evals/results/rag_eval_standins_run1.json`,
`rag_ablation_standins_run1.json`, `rag_verify_eval.json`. Setup: 3 synthetic personas x 200 dev jobs (510 ranked, 90 unfit),
embedder `hashing-512` and reranker `lexical-coverage` (OFFLINE STAND-INS, not semantic). Gold is generator-made, so compare
variants; do not quote absolute values as real-world accuracy. Real-model run (nomic-embed-text + bge-reranker-v2-m3) is pending
and will be recorded as run 2; conclusions marked (stand-in) may change.

## Table (overall; project_recall = diversity-aware)
| variant | recall@5 | project_recall@5 | mrr | ndcg@10 |
|---|---|---|---|---|
| v0 naive | 0.4441 | 0.4576 | 0.9542 | 0.4655 |
| v1 decompose | 0.6011 | 0.6314 | 0.8433 | 0.5527 |
| v2 hybrid | 0.5938 | 0.5595 | 0.9605 | 0.5796 |
| v3 rerank | 0.5987 | 0.5716 | 0.9637 | 0.5702 |
| v4 outcome | 0.4476 | 0.4619 | 0.9539 | 0.4656 |
| v5 multivector | 0.8377 | 0.3161 | 0.9155 | 0.6808 |
| v6 contextual | 0.5106 | 0.4466 | 0.951 | 0.5112 |
| v7 hyde (template) | 0.5765 | 0.5966 | 0.9678 | 0.5501 |
| v8 corrective | 0.5473 | 0.5377 | 0.9859 | 0.5403 |
| vbest (decompose+hybrid+rerank+corrective) | 0.6588 | 0.692 | 0.983 | 0.6144 |

## Hypotheses (docs/RD_B_hypothesis.md) vs result
- **H1 confirmed (stand-in).** V1 vs V0 on >=3-requirement jobs: project_recall@5 0.4388 -> 0.607 (+16.8 pts; threshold 15). Cost: MRR
  fell 0.954 -> 0.843 overall (decomposition spreads top ranks across requirements).
- **H2 direction right, size wrong.** Hybrid raised Recall@5 by 15 pts even with the hashing embedder; I predicted a small gap.
  Likely because the hashing embedder is weak on exact tool names. Re-test with nomic-embed-text before claiming a hybrid effect.
- **H3 rejected.** Rerank moved MRR only +0.01 but Recall@5 +15 pts. The lexical reranker over 30 candidates acts as an exact-term
  matcher. Recall@10 was not measured. Must be re-tested with the cross-encoder.
- **H4 not testable from this table.** V5 returns whole project bundles, so item Recall@5 (0.838) is inflated and project_recall
  collapses (0.316). Do not compare V5 on these metrics; implied-skill subset is not separated yet (TODO).
- **H5 rejected, with a caveat.** Template HyDE gained +14 pts project_recall; the template restates the required skills, so it is
  effectively a skills query, not real HyDE. Needs `--llm` run.
- **H6 confirmed on abstention** (unfit 1.0, fit 0.0) but unfit jobs are unfit by construction (skills absent from the corpus), so
  this is an easy case. Recall@5 did rise (0.444 -> 0.547) because the re-query adds exact-term evidence; H6 said it would not.
- **H7 confirmed.** V4 ~ V0 (0.4476 vs 0.4441). Its real test is reply rate (Phase 9).

## Ablation (full = vbest flags; project_recall@5 / recall@5)
full 0.692 / 0.6588. -decompose 0.6003 / 0.6344 (largest loss, -9.2 pts). -hybrid 0.6888 / 0.6573 (~0). -rerank 0.7381 / 0.6987
(IMPROVES, +4.6 / +4.0; MRR 0.983 -> 0.9744). -corrective 0.6943 / 0.6455 (retrieval ~same; loses abstention, 1.0 -> 0.0).

## Claim verification (deterministic verifier, rag_verify_eval.json)
- **Oracle evidence (verifier alone):** precision 1.0, recall 1.0, truthful claims kept 1.0 on all personas. Optimistic: templated claims.
- **Retrieved evidence (end to end):** fabrication recall 1.0 but precision 0.08-0.24 and truthful claims kept 0.70-0.82. The eval
  retrieves with title + skills only, so retrieval misses make the verifier remove true claims. Safe direction (over-removes), costly.
- **Do not quote "unsupported rate after = 0.0" as a result.** It is 0 by construction: repair removes every flagged claim and the
  same verifier re-checks. The independent number needs the LLM judge calibrated on owner labels (L1, Cohen's kappa).
- Flagged-per-proposal (0.37-0.43 oracle) exceeds planted fabrications (0.04-0.09) because generic sentences with a cue word
  are counted as factual claims; the planted-fabrication metrics are the clean ones.

## Decision (provisional, stand-in measurements)
Ship `rag.best` = decompose + hybrid + corrective (rerank off): best project_recall, lower latency, keeps abstention. Hybrid is kept
for exact-name robustness although the ablation shows ~0 here. Re-decide after run 2 with real models; rerank is expected to earn
its place only with a cross-encoder.

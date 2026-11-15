<!-- ai_ml: hashing-512 / lexical-coverage; fullstack_web: hashing-512 / lexical-coverage; data_analyst: hashing-512 / lexical-coverage -->
| variant | recall@5 | project_recall@5 | mrr | ndcg@10 | abstain_unfit | abstain_fit | latency_ms_p95 |
|---|---|---|---|---|---|---|---|
| v0_naive | 0.4441 | 0.4576 | 0.9542 | 0.4655 | 0.0 | 0.0 | 0.76 |
| v1_decompose | 0.6011 | 0.6314 | 0.8433 | 0.5527 | 0.0 | 0.0 | 0.61 |
| v2_hybrid | 0.5938 | 0.5595 | 0.9605 | 0.5796 | 0.0 | 0.0 | 1.05 |
| v3_rerank | 0.5987 | 0.5716 | 0.9637 | 0.5702 | 0.0 | 0.0 | 6.73 |
| v4_outcome | 0.4476 | 0.4619 | 0.9539 | 0.4656 | 0.0 | 0.0 | 0.81 |
| v5_multivector | 0.8377 | 0.3161 | 0.9155 | 0.6808 | 0.0 | 0.0 | 0.76 |
| v6_contextual | 0.5106 | 0.4466 | 0.951 | 0.5112 | 0.0 | 0.0 | 0.72 |
| v7_hyde | 0.5765 | 0.5966 | 0.9678 | 0.5501 | 0.0 | 0.0 | 0.61 |
| v8_corrective | 0.5473 | 0.5377 | 0.9859 | 0.5403 | 1.0 | 0.0 | 1.55 |
| vbest | 0.6588 | 0.692 | 0.983 | 0.6144 | 1.0 | 0.0 | 3.32 |

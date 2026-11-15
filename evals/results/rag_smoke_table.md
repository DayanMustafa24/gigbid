<!-- ai_ml: hashing-512 / lexical-coverage; fullstack_web: hashing-512 / lexical-coverage; data_analyst: hashing-512 / lexical-coverage -->
| variant | recall@5 | project_recall@5 | mrr | ndcg@10 | abstain_unfit | abstain_fit | latency_ms_p95 |
|---|---|---|---|---|---|---|---|
| v0_naive | 0.3827 | 0.4394 | 0.9622 | 0.4463 | 0.0 | 0.0 | 0.57 |
| v1_decompose | 0.6077 | 0.6757 | 0.8814 | 0.5631 | 0.0 | 0.0 | 0.73 |
| v2_hybrid | 0.6029 | 0.5617 | 0.9679 | 0.584 | 0.0 | 0.0 | 1.0 |
| v3_rerank | 0.6183 | 0.5881 | 0.9679 | 0.571 | 0.0 | 0.0 | 2.76 |
| v4_outcome | 0.3827 | 0.4287 | 0.9574 | 0.4456 | 0.0 | 0.0 | 0.6 |
| v5_multivector | 0.7923 | 0.2966 | 0.89 | 0.646 | 0.0 | 0.0 | 0.61 |
| v6_contextual | 0.5058 | 0.434 | 0.9712 | 0.5023 | 0.0 | 0.0 | 0.58 |
| v7_hyde | 0.5567 | 0.5842 | 0.9615 | 0.5296 | 0.0 | 0.0 | 0.44 |
| v8_corrective | 0.4798 | 0.5124 | 0.9808 | 0.5035 | 1.0 | 0.0 | 2.51 |
| vbest | 0.6779 | 0.6921 | 0.9904 | 0.6149 | 1.0 | 0.0 | 3.54 |

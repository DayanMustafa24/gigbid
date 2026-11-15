RAG variants (assignment Part B). All variants are presets (`Flags`) over ONE composable pipeline (`pipeline.py`), so every
variant shares the same code path and the ablation is a loop over `replace(best, <flag>=False)`.

| id | file | group | flag | targets |
|---|---|---|---|---|
| V0 | v0_naive.py | baseline | (none) | fixed chunks, dense top-k on the whole post |
| V1 | v1_decompose.py | Query | decompose | multi-requirement jobs |
| V2 | v2_hybrid.py | Retrieval | hybrid | exact tool names (BM25 + dense, RRF) |
| V3 | v3_rerank.py | Ranking | rerank | precision at small k |
| V4 | v4_outcome.py | Ranking | outcome | prefer evidence that won past bids |
| V5 | v5_multivector.py | Indexing | multivector | skills visible only in code |
| V6 | v6_contextual.py | Indexing | contextual | fragments that lose meaning alone |
| V7 | v7_hyde.py | Query | hyde | vague posts |
| V8 | v8_corrective.py | Agentic | corrective | grade, re-query, abstain |
| best | registry.py | | config `rag.best` | shipped pipeline, chosen by measurement |

Entry points: `rag.registry.build_context(persona_dir)` then `build_retriever(name, ctx).retrieve(JobQuery, k)`.
Offline defaults use stand-ins (hashing embedder, lexical reranker); results must say so.

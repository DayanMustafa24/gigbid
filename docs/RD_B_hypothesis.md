# R&D Question B: hypothesis (written BEFORE any evaluation run)

Date written: 2026-11-14. No `evals/results/rag_*.json` existed when this was written.

## Setting
Corpus: 3 synthetic personas (see DATA_CARD). Dev sets: 3 x 200 jobs with gold evidence by construction. Primary metrics:
Recall@5 (item level, capped at min(5, |gold|)), MRR, nDCG@10, and Project-Recall@5 (fraction of gold projects covered; this
is the diversity-aware metric because gold lists every item of a matching project).

## Hypotheses
- **H1** Requirement decomposition (V1) raises Project-Recall@5 over V0 by >= 15 points on jobs with >= 3 requirements, because
  one whole-post query is dominated by the first/loudest requirement. On 1-requirement jobs the gain is ~0.
- **H2** Hybrid (V2) beats dense-only on jobs naming exact tools. With the offline hashing embedder the gap is small (it is already
  lexical); with nomic-embed-text we expect >= 5 points Recall@5.
- **H3** Reranking (V3) improves MRR (precision at small k) but not Recall@10.
- **H4** Multi-vector (V5) helps most on implied skills (tool visible only in code); contextual (V6) helps README/code chunks.
- **H5** HyDE (V7) helps vague posts and hurts precise ones; template mode (no LLM) is expected to be ~neutral.
- **H6** Corrective (V8) abstains on >= 90% of unfit jobs while abstaining on <= 10% of fit jobs; it does not raise Recall@5.
- **H7** Outcome-aware ranking (V4) will NOT raise retrieval metrics (relevance gold ignores outcome); its value, if any, shows in
  reply rate (GigSim, Phase 9). Expected finding: better retrieval is not the same as a better reply rate.
- **Best pipeline (starting point)**: decompose + hybrid + rerank + corrective. Ablate one component at a time (`-decompose`,
  `-hybrid`, `-rerank`, `-corrective`); the component whose removal costs the most Project-Recall@5 is the most valuable.

## Falsification
H1 is rejected if the gain on >= 3-requirement jobs is < 5 points. The shipped variant is the best Project-Recall@5 / MRR
trade-off within the declared latency budget, decided from `evals/results/`, not from this file.

## Caveats written in advance
Offline numbers use the hashing embedder and lexical reranker stand-ins and must be labelled as such; real-model numbers
(Ollama nomic-embed-text, bge-reranker-v2-m3) are produced by the same command with config changed. Gold is generator-made.

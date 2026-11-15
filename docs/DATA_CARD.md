# Data Card

Status: IN PROGRESS (Phase 1). Sections marked TODO are filled when the corresponding generator exists.

## 1. Sources and real/synthetic split

| Dataset | Origin | Real? | Used for | Licence / attribution |
|---|---|---|---|---|
| Upwork job postings 2024 (50k records) | Kaggle `asaniczka/upwork-job-postings-dataset-2024-50k-records`, posts Dec 2023 - Feb 2024 | REAL | price statistics, realistic job text/skills | Licence: Open Data Commons Attribution License (ODC-By) v1.0 (as stated on the Kaggle page, confirmed by owner), https://opendatacommons.org/licenses/by/1-0/index.html . Attribution: dataset "Upwork Job Postings Dataset 2024 (50K Records)", Kaggle user `asaniczka`, https://www.kaggle.com/datasets/asaniczka/upwork-job-postings-dataset-2024-50k-records |
| Persona corpora (3 personas) | `scripts/synth/` (this repo) | SYNTHETIC | portfolio RAG corpus, past proposals with outcomes | own work; every record has `synthetic: true` |
| Dev sets (3 x 200), job stream (1,500), clients, scams, duplicates, injections | `scripts/synth/build_jobs.py` | SYNTHETIC (budgets/bands from real price stats) | evaluation | own work |

The instructor's `gigsim/`, `data/personas/`, official policy pack and dev sets were NOT in the starter kit. Stand-ins
live in `data/synthetic/` and `gigsim_local/`; the instructor's files drop into the required paths untouched.

## 2. Synthetic personas (`python -m scripts.synth.build_personas`)

- Three invented freelancers: `ai_ml` (Priya Nair), `fullstack_web` (Daniel Okafor), `data_analyst` (Sofia Marchetti).
  All names, clients, numbers and outcomes are invented. No real person or company is described.
- Per persona: 16 projects authored as structured facts (`scripts/synth/personas.py`); 16 case studies, 16 repos
  (README + code), CV (md + PDF), ~11-13 client reviews, 150 past proposals with outcome won/replied/ignored.
- Deterministic: seed 20261114 gives byte-identical files. Gold evidence labels are known BY CONSTRUCTION
  (`evidence.jsonl`: `gold_skills` = every skill the project truly demonstrates, `named_skills` = skills visible in
  that document's text).
- Built to defeat naive RAG, as the assignment asks: the same project appears as case study, README, code, CV
  bullet and review in different vocabulary; "implied" skills (e.g. pgvector, Celery) appear only in code, stack lines
  or reviews; ~40% of READMEs are generic and only the code reveals the skills.
- Past-proposal outcomes come from a hidden latent score (relevance, specific opener, cited evidence, length,
  price ratio, noise) with thresholds giving roughly 8% won / 28% replied / 64% ignored. This is a MODEL, not
  observed behaviour: any "outcome-aware ranking gains" measured on it show the method works when outcomes depend
  on those features, not that they do on Upwork. The write-up must say so.
- About 8% of proposals contain one deliberately fabricated claim (labelled `fabricated: true`), used to
  test claim verification.

## 3. Real data handling (Upwork CSV)

- `scripts/parse_upwork.py` splits the scraped description blob into body / budget / category / skills / country,
  de-duplicates skills, tags a `persona_domain` and computes price statistics (`data/reference/price_stats.json`).
- Raw CSV stays in git-ignored `data/external/`. Only aggregates are committed.
- Limitations: Dec 2023 - Feb 2024 only; prices are posted budgets, not paid amounts; `persona_domain` is keyword based
  (known weakness: "sql"/"api" skills pull web jobs into data_analyst).

## 4. Known limitations

- Synthetic text is template-based (no LLM), so lexical variety is lower than real proposals.
- ODC-By requires attribution (given in section 1); only aggregates derived from the dataset are committed.

## 5. Dev sets and job stream (`python -m scripts.synth.build_jobs`)

- Per persona 200 dev jobs: 110 fit (bid), 10 fit with a planted injection (bid, instruction must be ignored), 30 unfit
  skills (skip), 20 budget below floor (skip), 20 scams of 5 types (skip), 10 near-duplicate reposts (skip).
- Gold labels are derived by construction (no LLM): action, scam + type, injection + kind + position, duplicate_of,
  per-requirement gold evidence ids (from `evidence.jsonl` gold_skills), fair price band (real persona price quartiles
  from `price_stats.json` scaled by requirement count; hourly band = rate x 0.85-1.4).
- Job stream: 1,500 jobs (500 per persona, different seed, no labels) with gold in a sidecar `job_stream_gold.jsonl`.
- Limitations: templated text; scam/injection phrasing is a fixed catalogue of 5 + 4 patterns, so detectors tuned on it
  will overestimate real-world recall. Real-world robustness must be argued separately (failure analysis).

## 6. RAG evaluation use of this data (Phase 4)

- Corpus per persona = case studies, READMEs, code files, CV bullets, reviews (citable) + 150 past proposals (signal only).
  Ingestion never reads `gold_skills`; gold for retrieval = per-requirement evidence ids in the dev sets.
- Claim verification eval uses the generator's per-claim labels (`supported_by`, ~8% proposals with a planted fabricated claim).
- Gold lists every item of a matching project, so item-level Recall@5 alone favours returning five items of one project;
  Project-Recall@5 is reported beside it.
- Offline runs use a hashing embedder and lexical reranker (stand-ins, named in each results file). Real-model runs
  (Ollama nomic-embed-text, bge-reranker-v2-m3) use the same command with config changed.
- Limitations: gold and corpus come from the same generator (shared vocabulary), so absolute scores are optimistic;
  compare variants, do not quote absolute numbers as real-world accuracy.

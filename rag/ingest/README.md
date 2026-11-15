Ingestion (`loaders.py`): `load_persona(dir) -> Corpus`. No LLM, no network, never reads `gold_skills`.

Layouts: CATALOGUED (`evidence.jsonl` present: synthetic personas) and SCANNED (plain folder: `case_studies/*.md`,
`repos/<slug>/README.md` + code, `cv.md|cv.pdf`, `reviews.jsonl`, `proposals.jsonl`), so you can point it at your own portfolio.
Metadata per item: persona, type, project, skills, date, client industry, proposal outcome, rating.
Incremental re-index: `old_corpus.diff(new_corpus)` -> added/changed/removed ids; the embedding cache is content-addressed
(`data/processed/emb_cache`, git-ignored), so only unseen text is embedded.

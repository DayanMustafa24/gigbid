# ADR 0003: Rules, costs, prompts and models are data, not code

Status: accepted (2026-11-14)

Context: GigSim will release a platform policy update (new rule or cost). Evaluation counts how
cheaply the system absorbs it.

Decision: policy rules and costs live in `agent/policy/rules.yaml` (deterministic engine in
`agent/policy/engine.py`), model routing and thresholds in `agent/config.yaml`, prompts as
versioned markdown in `agent/prompts/`. Node code reads these; it contains no rule text.

Consequences: a new rule = a YAML entry + a test case. The diff size for the change request is
recorded in the R&D report.

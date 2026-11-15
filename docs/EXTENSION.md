# Signature Extension: X2 Proposal experiments

**What:** GigBid runs controlled A/B tests of proposal strategies and reports whether the difference in reply rate is statistically real.

## Design
* Strategies are data: `agent/config.yaml` -> `strategies` (`evidence-first`, `question-first`). Adding an arm = one line of config, no code.
* Assignment is deterministic: `agent/nodes/writer.pick_strategy` = hash(job_id) over the sorted strategy names. Same job, same arm, always
  (reproducible, no hidden randomness); the arm is written into `ProposalDraft.strategy` and into the trace.
* The writer prompt (`agent/prompts/writer.md`) receives the arm as `<opener>`; the opener never overrides the grounding rules, so verification,
  Critic and Compliance still apply to every arm.
* Outcomes: `MemoryStore.record_outcome(..., strategy=...)` stores replied/won per proposal; `strategy_table` counts them per arm.
* Statistics: `agent/memory/stats.two_proportion_z` (pooled two-proportion z-test, two-sided). `GET /analytics/summary` returns
  `experiment: {arms, test}` pooled over personas; `test` is `null` until exactly two arms have outcomes. No rate is ever estimated without data.

## Offline evaluation (GigSim stand-in)
`uv run python -m evals.runners.x2_experiment --sample 120 --seed 42` drafts proposals with the shipped graph on dev jobs whose gold action is
`bid`, lets the seeded GigSim client model (`gigsim_local/client_model.py`, hidden preferences) judge each one, and tests reply rate between arms.
Writes the experiment JSON and a markdown table into the results folder (`x2_experiment*`).

## Honest limits
* The client model is a rule-based stand-in: a result describes the simulator's preferences, not real clients.
* Power: 120 jobs split in two arms detects only large effects; a non-significant result is reported as such, not as "no difference".
* Without a model key the template writer ignores the strategy, so the runner refuses to run instead of reporting two identical arms.
* Live mode (X6, post-assignment) lets the same analytics run on real outcomes.

## Results
See the `x2_experiment_table.md` file in the results folder once the experiment has been run (numbers are quoted only from that file).

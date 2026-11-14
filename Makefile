.PHONY: install dev api test lint eval seed demo gigsim
install:
	uv sync --extra dev

dev: api

api:
	uv run uvicorn apps.api.main:app --reload --port 8000

test:
	uv run pytest -q

lint:
	uv run ruff check .

eval:
	uv run python -m evals.runners.run_demo_eval

# Phase 1 data layer (deterministic). Needs `uv sync --extra dev --extra data`.
seed:
	uv run python -m scripts.parse_upwork
	uv run python -m scripts.synth.build_personas
	uv run python -m scripts.synth.build_jobs

demo:
	@echo "TODO Phase 8: docker compose -f infra/docker-compose.yml up"

# Phase 2: local marketplace simulator on :8081 (set GIGSIM_DATASET=sample to run without `make seed`)
gigsim:
	uv run python -m gigsim_local

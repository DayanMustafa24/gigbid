# syntax=docker/dockerfile:1
# One image for every Python service (api, worker, gigsim, 3 MCP servers): same code, different command (see infra/docker-compose.yml).
# Build context = repo root:  docker build -f infra/docker/python.Dockerfile .
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
RUN pip install uv
WORKDIR /app

# dependencies first (cached until pyproject/uv.lock change). obs = Phoenix/OpenTelemetry tracing.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra obs --no-install-project

COPY agent ./agent
COPY apps/api ./apps/api
COPY gigsim_local ./gigsim_local
COPY mcp_servers ./mcp_servers
COPY rag ./rag
COPY worker ./worker
COPY scripts ./scripts
COPY data ./data
COPY evals/datasets ./evals/datasets
COPY alembic.ini ./

# non-root; writable state dirs are mounted as named volumes by compose (they inherit this ownership)
RUN useradd --system --uid 10001 --create-home app \
    && mkdir -p data/app data/memory data/checkpoints data/audit data/workspace data/user \
    && chown -R app:app /app/data
USER app
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

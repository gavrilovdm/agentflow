# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# git: workspace clones; node/npm: gate for TypeScript target repos
RUN apt-get update && apt-get install -y --no-install-recommends git nodejs npm \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /app

FROM base AS deps
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

FROM base AS runtime
RUN useradd --create-home --uid 10001 agentflow
COPY --from=deps /app/.venv /app/.venv
COPY src ./src
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
USER agentflow
EXPOSE 8000
CMD ["uvicorn", "agentflow.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

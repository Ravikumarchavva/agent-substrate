# syntax=docker/dockerfile:1.7
# Embedding + reranking service: a thin httpx proxy to the llama-embed / llama-rerank sidecars (deploy/docker/llama-server*.Dockerfile).
# No model library in here; the models run in llama-server processes.
#
# Build (context = monorepo root):
#   docker build -f deploy/docker/embedding-reranker.Dockerfile -t embedding-reranker:local .
# Run: docker run -p 8080:8080 embedding-reranker:local

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS build
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Every workspace member's manifest is needed to resolve the single root lock; sources come in the second step.
COPY pyproject.toml uv.lock ./
COPY agents/agent-substrate/pyproject.toml agents/agent-substrate/
COPY agents/substrate-cloud/pyproject.toml agents/substrate-cloud/
COPY data/substrate-data/pyproject.toml data/substrate-data/
COPY data/substrate-data-client/pyproject.toml data/substrate-data-client/
COPY data/embedding-reranker/pyproject.toml data/embedding-reranker/
COPY data/inference-pool/pyproject.toml data/inference-pool/
COPY identity/substrate-identity/pyproject.toml identity/substrate-identity/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package embedding-reranker
COPY agents/agent-substrate agents/agent-substrate
COPY data/inference-pool data/inference-pool
COPY data/embedding-reranker data/embedding-reranker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package embedding-reranker

FROM python:3.14-slim-bookworm AS runtime
RUN useradd --system --uid 10001 --create-home app
COPY --from=build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/v1/health', timeout=3)"]
ENTRYPOINT ["uvicorn", "embedding_reranker.app:app"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]

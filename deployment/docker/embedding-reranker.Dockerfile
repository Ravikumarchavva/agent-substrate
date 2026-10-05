# docker/embedding-reranker.Dockerfile — Embedding + reranking service
#
# Build:   docker build -f docker/embedding-reranker.Dockerfile -t embedding-reranker:latest .
# Run:     docker run -p 8080:8080 embedding-reranker:latest
#
# Multimodal embedding and reranking (Qwen3-VL-Embedding-2B /
# Qwen3-VL-Reranker-2B via the llama-embed/llama-rerank llama-server
# sidecars, see docs/claude_docs/decisions.md) — a thin httpx proxy, no
# local model, no heavy dependencies of its own (apps/embedding-reranker/pyproject.toml). Split out of document-intelligence since it
# shares no code or state with the OCR/layout pipeline.

FROM python:3.14-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY packages/inference-pool ./packages/inference-pool
COPY apps/embedding-reranker ./apps/embedding-reranker
RUN uv pip install --system -e "./apps/embedding-reranker"

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/v1/health || exit 1

ENTRYPOINT ["uvicorn", "embedding_reranker.app:app"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]

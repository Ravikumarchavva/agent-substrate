# docker/embedding-reranker.Dockerfile — Embedding + reranking service
#
# Build:   docker build -f docker/embedding-reranker.Dockerfile -t embedding-reranker:latest .
# Run:     docker run -p 8080:8080 embedding-reranker:latest
#
# Multimodal embedding and reranking (Qwen3-VL-Embedding-2B /
# Qwen3-VL-Reranker-2B via the llama-embed/llama-rerank llama-server
# sidecars, see docs/claude_docs/decisions.md) — a thin httpx proxy, no
# local model, no heavy dependencies of its own (see pyproject.toml: no
# dedicated extra needed). Split out of document-intelligence since it
# shares no code or state with the OCR/layout pipeline.

FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system -e ".[server]"

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/v1/health || exit 1

ENTRYPOINT ["uvicorn", "substrate.runtimes.embedding_reranker.service.app:app"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]

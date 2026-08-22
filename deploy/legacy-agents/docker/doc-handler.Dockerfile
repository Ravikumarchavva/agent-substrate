# docker/doc-handler.Dockerfile — Document extraction service
#
# Build:   docker build -f docker/doc-handler.Dockerfile -t doc-handler:latest .
# Run:     docker run -p 8080:8080 doc-handler:latest
#
# Layout-aware document parsing (PaddleOCR: chart/table detection + OCR).
# Multimodal embedding and reranking are NOT loaded in-process here — this
# service calls the llama-embed/llama-rerank sidecars over HTTP instead (see
# EmbeddingReranker in embedding.py and docs/claude_docs/decisions.md for
# why). CPU-only — paddlepaddle's CPU wheel (~185MB) is used, no CUDA
# runtime pulled in. Isolated from the main API image entirely — this is the
# only place these dependencies get installed.

FROM python:3.13-slim AS base

# libgomp1: paddle's compiled core (libpaddle.so) needs it directly and
# fails ImportError at startup without it. This was masked while `torch`
# was still a doc-handler-extra dependency (its wheel bundles its own
# libgomp copy) — surfaced as a real startup crash once torch was removed
# (see docs/claude_docs/decisions.md's Qwen3-VL entry for why).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    libgl1 libglib2.0-0 libxcb1 libxext6 libsm6 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system -e ".[doc-handler]"

EXPOSE 8080

# Generous --start-period: first boot loads the OCR/layout and
# embedding/reranker model weights (real latency, unlike code-interpreter's
# Firecracker pool which has no model to load).
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8080/v1/health || exit 1

ENTRYPOINT ["uvicorn", "substrate.doc_handler.service.app:app"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]

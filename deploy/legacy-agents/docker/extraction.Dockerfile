# docker/extraction.Dockerfile — Document extraction service
#
# Build:   docker build -f docker/extraction.Dockerfile -t extraction:latest .
# Run:     docker run -p 8080:8080 extraction:latest
#
# Layout-aware document parsing (PaddleOCR: chart/table detection + OCR) plus
# multimodal embedding (SigLIP) and reranking (MiniLM cross-encoder) via
# sentence-transformers (see the `extraction` extra's own comment in
# pyproject.toml). CPU-only — paddlepaddle's CPU wheel (~185MB) is used, no
# CUDA runtime pulled in. Isolated from the main API image entirely — this is
# the only place these dependencies get installed.

FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    libgl1 libglib2.0-0 libxcb1 libxext6 libsm6 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system -e ".[extraction]"

EXPOSE 8080

# Generous --start-period: first boot loads the OCR/layout and
# embedding/reranker model weights (real latency, unlike code-interpreter's
# Firecracker pool which has no model to load).
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8080/v1/health || exit 1

ENTRYPOINT ["uvicorn", "substrate.serving.services.extraction.app:app"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]

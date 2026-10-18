# docker/document-intelligence.Dockerfile — Document intelligence service
#
# Build:   docker build -f docker/document-intelligence.Dockerfile -t document-intelligence:latest .
# Run:     docker run -p 8080:8080 document-intelligence:latest
#
# Document server: the library's own Reader as the baseline for every format (PDFium, native Office/HTML),
# PaddleOCR layout/chart/table extraction for PDFs and images on `hi_res` or scanned pages, and an optional
# LibreOffice (not installed here) for legacy .doc/.ppt/.xls/.rtf — without it those get a clear failure.
# Multimodal embedding and reranking are a separate service now — see
# apps/embedding-reranker/ and docs/claude_docs/decisions.md for why.
# CPU-only — paddlepaddle's CPU wheel (~185MB) is used, no CUDA runtime
# pulled in. Isolated from the main API image entirely — this is the only
# place these dependencies get installed.

FROM python:3.13-slim AS base

# libgomp1: paddle's compiled core (libpaddle.so) needs it directly and
# fails ImportError at startup without it. This was masked while `torch`
# was still a document-intelligence-extra dependency (its wheel bundles its
# own libgomp copy) — surfaced as a real startup crash once torch was
# removed (see docs/claude_docs/decisions.md's Qwen3-VL entry for why).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    libgl1 libglib2.0-0 libxcb1 libxext6 libsm6 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY packages/inference-pool ./packages/inference-pool
COPY apps/document-intelligence ./apps/document-intelligence
RUN uv pip install --system -e "./apps/document-intelligence[paddle]"

EXPOSE 8080

# Generous --start-period: first boot loads the OCR/layout model weights
# (real latency, unlike code-interpreter's Firecracker pool which has no
# model to load).
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8080/v1/health || exit 1

ENTRYPOINT ["uvicorn", "document_intelligence.app:app"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]

# docker/document-intelligence.gpu.Dockerfile — Document intelligence service, GPU variant
#
# Build:   docker build -f deployment/docker/document-intelligence.gpu.Dockerfile -t document-intelligence-gpu:latest .
# Run:     docker compose --profile document-intelligence-gpu up
#
# Same service as document-intelligence.Dockerfile, but installs the CUDA
# 13.0 `paddlepaddle-gpu` wheel (document-intelligence-gpu extra,
# pyproject.toml) instead of the CPU one — for local dev on an NVIDIA GPU,
# not for cheap hosting (that's what document-intelligence.Dockerfile/the
# default `document-intelligence` profile is for). Requires the host's
# Docker to have the NVIDIA Container Toolkit configured (`docker info`
# lists an `nvidia` runtime) and a driver new enough for CUDA 13.0 — verify
# with `nvidia-smi` on the host before building. Unlike
# document-intelligence.Dockerfile's `python:3.13-slim` base, this starts
# from an nvidia/cuda runtime+cuDNN image (paddle needs the matching
# CUDA/cuDNN shared libs actually present, not just a driver) and installs
# Python 3.13 via uv since the CUDA base image ships none.

FROM nvidia/cuda:13.0.3-cudnn-runtime-ubuntu24.04 AS base

# Same native deps as document-intelligence.Dockerfile's base image, see
# there for why each is needed (libgomp1 for libpaddle.so, libgl1/libglib
# etc. for OpenCV used by PaddleX's image preprocessing).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    libgl1 libglib2.0-0 libxcb1 libxext6 libsm6 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
RUN uv python install 3.13

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv venv --python 3.13 /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
RUN uv pip install --python /opt/venv/bin/python -e ".[document-intelligence-gpu]"

EXPOSE 8080

# Generous --start-period: first boot loads the OCR/layout model weights
# (real latency, unlike code-interpreter's Firecracker pool which has no
# model to load).
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8080/v1/health || exit 1

ENTRYPOINT ["uvicorn", "substrate.runtimes.document_intelligence.service.app:app"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]

# syntax=docker/dockerfile:1.7
# Document intelligence service (Python 3.13, CPU PaddleOCR). Not a uv workspace member: PaddlePaddle publishes no
# 3.14 wheel, so it keeps its own project and (once committed) its own data/document-intelligence/uv.lock. Without a
# lock the build resolves at build time; CI should commit the lock and this file then installs it with --frozen.
#
# Build (context = monorepo root):
#   docker build -f deploy/docker/document-intelligence.Dockerfile -t document-intelligence:local .
# Run: docker run -p 8080:8080 document-intelligence:local

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# The project's path sources are ../../agents/agent-substrate and ../inference-pool, so keep the repo layout.
COPY agents/agent-substrate agents/agent-substrate
COPY data/inference-pool data/inference-pool
COPY data/document-intelligence data/document-intelligence
WORKDIR /app/data/document-intelligence
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -f uv.lock ]; then uv sync --frozen --no-dev --no-editable --extra paddle; \
    else uv sync --no-dev --no-editable --extra paddle; fi

FROM python:3.14-slim-bookworm AS runtime
# libgomp1: paddle's compiled core needs it directly. The rest are OpenCV's runtime libraries.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libxcb1 libxext6 libsm6 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home app \
    && mkdir -p /models && chown app:app /models
# Same path as in the build stage: the venv scripts hard-code it.
COPY --from=build --chown=app:app /app/data/document-intelligence/.venv /app/data/document-intelligence/.venv
ENV PATH="/app/data/document-intelligence/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
WORKDIR /home/app
EXPOSE 8080
# Generous start period: first boot loads the OCR/layout model weights.
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/v1/health', timeout=3)"]
ENTRYPOINT ["uvicorn", "document_intelligence.app:app"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]

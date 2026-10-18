# syntax=docker/dockerfile:1.7
# The agent server (substrate-cloud): the monolith, or any one of the microservices, from one image.
# Which one runs is decided at start by SUBSTRATE_MODE (monolith | microservices) and, for microservices,
# SUBSTRATE_SERVICE (see deploy/docker/agent-server-entrypoint.sh and deploy/PORTS.md).
#
# Build (context = monorepo root):
#   docker build -f deploy/docker/agent-server.Dockerfile -t substrate-agent-server:local .
#
# The code interpreter's default runtime is nsjail, which needs extra container privileges and fails closed
# without them. The image carries the binary but runs as an unprivileged user: in Kubernetes set
# SANDBOX_RUNTIME=k8s (isolated pods, optionally a gVisor RuntimeClass); on a single box either grant the
# capabilities in the compose bundle's "nsjail" override or accept the in-compose default (see deploy/compose).

# ── nsjail, built from source in a throwaway stage (no Debian package exists) ──
FROM debian:bookworm-slim AS nsjail-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        autoconf bison ca-certificates flex g++ gcc git libprotobuf-dev libnl-route-3-dev libtool make pkg-config protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --branch 3.6 --depth 1 https://github.com/google/nsjail.git /nsjail \
    && cd /nsjail && git submodule update --init --recursive && make clean && make

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
    uv sync --frozen --no-dev --no-install-workspace --package substrate-cloud
COPY agents/agent-substrate agents/agent-substrate
COPY agents/substrate-cloud agents/substrate-cloud
COPY data/substrate-data-client data/substrate-data-client
COPY identity/substrate-identity identity/substrate-identity
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package substrate-cloud

FROM python:3.14-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libprotobuf32 libnl-route-3-200 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home app \
    && mkdir -p /data && chown app:app /data
COPY --from=nsjail-builder /nsjail/nsjail /usr/local/bin/nsjail
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --chmod=755 deploy/docker/agent-server-entrypoint.sh /usr/local/bin/agent-server-entrypoint
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data SUBSTRATE_MODE=monolith
USER app
WORKDIR /data
EXPOSE 8000
# 8000 is the monolith and the default; a microservice listens on PORT (the entrypoint sets its documented default).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"]
ENTRYPOINT ["agent-server-entrypoint"]

# syntax=docker/dockerfile:1.7
# loamz-agents: durable agents, tools and agent server.
#
# Runs either all roles (monolith) or a subset chosen by LOAMZ_ROLES (microservices / role-split).
#
# Build (context = monorepo root):
#   docker build -f deploy/docker/loamz-agents.Dockerfile -t loamz-agents:local .

FROM debian:bookworm-slim AS nsjail-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        autoconf bison ca-certificates flex g++ gcc git libprotobuf-dev libnl-route-3-dev libtool make pkg-config protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --branch 3.6 --depth 1 https://github.com/google/nsjail.git /nsjail \
    && cd /nsjail && git submodule update --init --recursive && make clean && make

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS build
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY server/engine/pyproject.toml server/engine/
COPY server/server/pyproject.toml server/server/
COPY sdk/python/core/pyproject.toml sdk/python/core/
COPY sdk/python/data/pyproject.toml sdk/python/data/
COPY meta/loamz/pyproject.toml meta/loamz/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package loamz-server --extra agents

COPY server/engine server/engine
COPY server/server server/server
COPY sdk/python/core sdk/python/core
COPY sdk/python/data sdk/python/data

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package loamz-server --extra agents

FROM python:3.14-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libprotobuf32 libnl-route-3-200 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home app \
    && mkdir -p /data && chown app:app /data

COPY --from=nsjail-builder /nsjail/nsjail /usr/local/bin/nsjail
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --chmod=755 deploy/docker/loamz-agents-entrypoint.sh /usr/local/bin/loamz-agents-entrypoint

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data
USER app
WORKDIR /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"]

ENTRYPOINT ["loamz-agents-entrypoint"]


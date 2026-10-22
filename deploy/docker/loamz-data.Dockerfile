# syntax=docker/dockerfile:1.7
# loamz-data: the control plane API, compute controller, and attached compute workers.
#
# Roles:
#   serve                      control plane API, :8020
#   controller                 compute controller, :8014
#   worker --role sql|jobs|documents|inference   attached compute, :8011
#   attach --name N --role R --url U --token-env VAR   register a running worker
#
# Build (context = monorepo root):
#   docker build -f deploy/docker/loamz-data.Dockerfile -t loamz-data:local .

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS build
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY server/engine/pyproject.toml server/engine/
COPY server/server/pyproject.toml server/server/
COPY sdk/python/core/pyproject.toml sdk/python/core/
COPY sdk/python/identity/pyproject.toml sdk/python/identity/
COPY sdk/python/data/pyproject.toml sdk/python/data/
COPY meta/loamz/pyproject.toml meta/loamz/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package loamz-server

COPY server/engine server/engine
COPY server/server server/server
COPY sdk/python/core sdk/python/core
COPY sdk/python/identity sdk/python/identity
COPY sdk/python/data sdk/python/data

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package loamz-server

ENV HOME=/opt/loamz/home
RUN mkdir -p $HOME && /app/.venv/bin/python -c "import duckdb; duckdb.connect().execute('INSTALL httpfs')"

FROM python:3.14-slim-bookworm AS runtime
RUN useradd --system --uid 10001 --home-dir /opt/loamz/home app
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --from=build --chown=app:app /opt/loamz/home /opt/loamz/home
RUN mkdir -p /var/lib/loamz-data && chown app:app /var/lib/loamz-data

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/opt/loamz/home \
    LOAMZ_DATA_SCRATCH=/var/lib/loamz-data/tmp LOAMZ_DATA_METADATA=/var/lib/loamz-data/catalog.sqlite \
    LOAMZ_DATA_CONTROLLER_STATE=/var/lib/loamz-data/compute

USER app
WORKDIR /var/lib/loamz-data
EXPOSE 8020 8014 8011

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/readyz' % os.environ.get('PORT', '8020'), timeout=4)"]

ENTRYPOINT ["loamz-server", "data"]
CMD ["serve", "--host", "0.0.0.0"]


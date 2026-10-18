# syntax=docker/dockerfile:1.7
# substrate-data: the control plane API, the compute controller, and the attached-compute workers, one image.
# The command picks the role:
#   serve                      control plane API, :8020
#   controller                 compute controller, :8014
#   worker --role sql|jobs|rag|inference   attached compute, :8011 (set SUBSTRATE_DATA_WORKER_BIND_HOST=0.0.0.0)
#   attach --name N --role R --url U --token-env VAR   register a running worker with the controller
# Everything else is SUBSTRATE_DATA_* environment (data/substrate-data/.env.example).
#
# Build (context = monorepo root):
#   docker build -f deploy/docker/substrate-data.Dockerfile -t substrate-data:local .

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS build
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY agents/agent-substrate/pyproject.toml agents/agent-substrate/
COPY agents/substrate-cloud/pyproject.toml agents/substrate-cloud/
COPY data/substrate-data/pyproject.toml data/substrate-data/
COPY data/substrate-data-client/pyproject.toml data/substrate-data-client/
COPY data/embedding-reranker/pyproject.toml data/embedding-reranker/
COPY data/inference-pool/pyproject.toml data/inference-pool/
COPY identity/substrate-identity/pyproject.toml identity/substrate-identity/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package substrate-data
# substrate-data runs the agent library's document and memory engines (documents/ in substrate-data), so it ships that source too.
COPY agents/agent-substrate agents/agent-substrate
COPY data/substrate-data data/substrate-data
COPY data/substrate-data-client data/substrate-data-client
COPY identity/substrate-identity identity/substrate-identity
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package substrate-data
# DuckDB extensions are installed at build time: workers never download code at runtime.
ENV HOME=/opt/substrate/home
RUN mkdir -p $HOME && /app/.venv/bin/python -c "import duckdb; duckdb.connect().execute('INSTALL httpfs')"

FROM python:3.14-slim-bookworm AS runtime
RUN useradd --system --uid 10001 --home-dir /opt/substrate/home app
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --from=build --chown=app:app /opt/substrate/home /opt/substrate/home
RUN mkdir -p /var/lib/substrate-data && chown app:app /var/lib/substrate-data
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/opt/substrate/home \
    SUBSTRATE_DATA_SCRATCH=/var/lib/substrate-data/tmp SUBSTRATE_DATA_METADATA=/var/lib/substrate-data/catalog.sqlite \
    SUBSTRATE_DATA_CONTROLLER_STATE=/var/lib/substrate-data/compute
USER app
WORKDIR /var/lib/substrate-data
EXPOSE 8020 8014 8011
# The probe asks the port this container serves: PORT for the role, else the API's.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/readyz' % os.environ.get('PORT', '8020'), timeout=4)"]
ENTRYPOINT ["substrate-data"]
CMD ["serve", "--host", "0.0.0.0"]

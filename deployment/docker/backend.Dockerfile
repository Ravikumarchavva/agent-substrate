# syntax=docker/dockerfile:1.7

# ── nsjail builder ───────────────────────────────────────────────────────────
# No apt/Debian package or prebuilt binary exists for nsjail
# (github.com/google/nsjail) — build from source, pinned to a known-good tag,
# in its own throwaway stage so the final image doesn't carry the build
# toolchain (autoconf/bison/flex/...). Mirrors nsjail's own upstream
# Dockerfile exactly (base image, deps, build command).
FROM debian:bookworm-slim AS nsjail-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf \
    bison \
    ca-certificates \
    flex \
    g++ \
    gcc \
    git \
    libprotobuf-dev \
    libnl-route-3-dev \
    libtool \
    make \
    pkg-config \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --branch 3.6 --depth 1 https://github.com/google/nsjail.git /nsjail \
    && cd /nsjail && git submodule update --init --recursive \
    && make clean && make

# Backend Dockerfile for Python FastAPI
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS base

# Install system dependencies.
# nsjail: the default SANDBOX_RUNTIME isolates agent-generated code in Linux
# namespaces + cgroups scoped to one session directory (no daemon, no root).
# Runtime shared libs (libprotobuf32/libnl-route-3-200) match nsjail's own
# upstream Dockerfile. Running it *inside* this container additionally needs
# cgroup: host (for its cgroup limits to initialize) and, for the
# unprivileged user namespaces it also relies on, cap_add=SYS_ADMIN and
# seccomp=unconfined on the container itself — see docker-compose.yml.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    g++ \
    libprotobuf32 \
    libnl-route-3-200 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=nsjail-builder /nsjail/nsjail /usr/local/bin/nsjail

WORKDIR /app

ENV UV_LINK_MODE=copy


# Copy dependency files
# The platform is an app (apps/substrate-cloud) that depends on the library at the repository root: install both from their
# lock files, dependencies first so the layer is cached until a lock file changes.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY apps/substrate-cloud/pyproject.toml apps/substrate-cloud/uv.lock apps/substrate-cloud/
WORKDIR /app/apps/substrate-cloud
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY apps/substrate-cloud ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV PATH="/app/apps/substrate-cloud/.venv/bin:$PATH"

RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["uvicorn", "substrate_cloud.monolith.app:app", "--host", "0.0.0.0", "--port", "8000"]

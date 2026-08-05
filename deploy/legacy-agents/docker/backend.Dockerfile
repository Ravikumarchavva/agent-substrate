# syntax=docker/dockerfile:1.7

# Backend Dockerfile for Python FastAPI
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS base

# Install system dependencies.
# bubblewrap: the default SANDBOX_RUNTIME isolates agent-generated code in a
# Linux namespace scoped to one session directory (no daemon, no root). Running
# it *inside* this container additionally needs cap_add=SYS_ADMIN and
# seccomp=unconfined on the container itself — see docker-compose.deploy.yml.
RUN apt-get update && apt-get install -y \
    curl \
    gcc \
    g++ \
    bubblewrap \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"
ENV UV_LINK_MODE=copy


# Copy dependency files
COPY pyproject.toml ./
COPY uv.lock ./
COPY README.md ./

# Install locked dependencies before copying application source so this layer
# stays cached across normal code changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy application code and install just the project on top of the cached env.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Create non-root user
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["uvicorn", "agent_substrateserver.app:app", "--host", "0.0.0.0", "--port", "8000"]

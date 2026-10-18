"""Server-layer configuration.

``ServerSettings`` extends ``SubstrateConfig`` with fields that only the FastAPI
server needs: OIDC identity, CORS, rate limiting, observability, and feature flags.

It auto-loads ``.env`` from the current working directory (where you run
``uv run start``).  For library use, import ``SubstrateConfig`` from
``substrate_cloud.config`` instead.
"""

from __future__ import annotations

from typing import List

from pydantic_settings import SettingsConfigDict

from substrate_cloud.config import SubstrateConfig


class ServerSettings(SubstrateConfig):
    # ── File storage: limits ─────────────────────────────────────────────────
    FILE_MAX_UPLOAD_BYTES: int = 200 * 1024 * 1024
    # An upload whose eager staging (reading and filing the document) started but never finished -- the in-process task it runs on does not
    # survive a restart -- is staged again by the next message that needs it once it is this old.
    STAGING_RECONCILIATION_TTL_MINUTES: float = 10.0
    WORKSPACE_USER_QUOTA_BYTES: int = 1024 * 1024 * 1024
    WORKSPACE_USER_DELETE_ALLOWED: bool = True

    # ── RAG daily limits (multi-tenant abuse controls) ──────────────────────────
    RAG_DAILY_DOC_LIMIT: int = 20
    RAG_DAILY_UPLOAD_ATTEMPT_LIMIT: int = 100

    FRONTEND_URL: str = "http://127.0.0.1:3000"

    # ── CORS ─────────────────────────────────────────────────────────────────
    CORS_ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
    ]

    # ── HTTP rate limiting ───────────────────────────────────────────────────
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_AUTHED_RPM: int = 60
    RATE_LIMIT_ANON_RPM: int = 5
    RATE_LIMIT_WINDOW_SECONDS: int = 86400
    PORTFOLIO_RATE_LIMIT_RPM: int = 10
    # False (default) = fail closed: 503 when Redis is unavailable instead of
    # silently allowing unlimited traffic. Set True only for dev/single-user.
    RATE_LIMIT_FAIL_OPEN: bool = False

    # ── Microservices mode ───────────────────────────────────────────────────
    # Where a service finds its peers: ``{name}`` is the service in dashes (``job-controller``), ``{port}`` its port from
    # deploy/PORTS.md. ``<NAME>_SERVICE_URL`` (``JOB_CONTROLLER_SERVICE_URL``) overrides one peer. Compose and Helm set the template.
    SERVICE_URL_TEMPLATE: str = "http://localhost:{port}"

    # ── Observability ────────────────────────────────────────────────────────
    OTLP_ENDPOINT: str = "http://localhost:4318"

    # ── Feature flags ────────────────────────────────────────────────────────
    ENABLE_BUILDER: bool = False
    # Multimodal input safety guardrail (agents/middleware/guardrails/
    # multimodal_safety.py) — jailbreak/prompt-attack + NSFW image scoring
    # on every live chat turn. True by default; the only reason to disable
    # is dev/testing without the ~280MB Prompt Guard model downloaded, or a
    # deployment that hasn't reviewed the licensing note (see the
    # implementation plan) yet.
    ENABLE_TEXT_SAFETY_GUARD: bool = True
    SAFETY_TEXT_THRESHOLD: float = 0.9
    SAFETY_IMAGE_NSFW_THRESHOLD: float = 0.5
    SAFETY_IMAGE_NSFL_THRESHOLD: float = 0.3

    model_config = SettingsConfigDict(
        env_file=".env",  # relative to CWD — where `uv run start` is invoked
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )


# Server-layer singleton — only import this from substrate_cloud/ code.
settings = ServerSettings()

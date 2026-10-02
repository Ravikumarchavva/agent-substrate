"""Server-layer configuration.

``ServerSettings`` extends ``SubstrateConfig`` with fields that only the FastAPI
server needs: JWT auth, CORS, rate limiting, observability, and feature flags.

It auto-loads ``.env`` from the current working directory (where you run
``uv run start``).  For library use, import ``SubstrateConfig`` from
``substrate.config`` instead.
"""

from __future__ import annotations

from typing import List

from pydantic import field_validator, model_validator
from pydantic_settings import SettingsConfigDict

from substrate.config import SubstrateConfig


class ServerSettings(SubstrateConfig):
    # ── File storage: encryption, quotas, pending-upload staging ───────────────
    FILE_ENCRYPTION_MODE: str = "none"
    FILE_KEK_HEX: str = ""
    # Attachments live here (local disk, never the real FILE_STORE_BACKEND)
    # from upload until the message carrying them is actually sent — see
    # integrations/storage/pending.py. Swept periodically; PENDING_UPLOAD_TTL_HOURS
    # is how long an abandoned (never-sent) attachment survives before removal.
    PENDING_UPLOAD_LOCAL_PATH: str = ""
    PENDING_UPLOAD_TTL_HOURS: float = 24.0

    @model_validator(mode="after")
    def _apply_server_defaults(self) -> ServerSettings:
        root = self.DATA_DIR.rstrip("/")
        if not self.PENDING_UPLOAD_LOCAL_PATH:
            self.PENDING_UPLOAD_LOCAL_PATH = f"{root}/blobs/pending"
        return self
    FILE_MAX_UPLOAD_BYTES: int = 200 * 1024 * 1024
    # routes/files.py::sweep_stuck_staging_uploads -- a startup-time
    # reconciliation pass for uploads whose eager staging (extraction +
    # embedding) started but never finished before a server restart (the
    # in-process asyncio.create_task it runs on has no durability across
    # one). A real upload is never "in flight" for anywhere near this long
    # under normal operation, so this only ever fires for genuinely
    # abandoned, restart-orphaned work -- turns "stuck forever" into
    # "delayed by up to one restart."
    STAGING_RECONCILIATION_TTL_MINUTES: float = 10.0
    WORKSPACE_USER_QUOTA_BYTES: int = 1024 * 1024 * 1024
    WORKSPACE_USER_DELETE_ALLOWED: bool = True

    # ── RAG daily limits (multi-tenant abuse controls) ──────────────────────────
    RAG_DAILY_DOC_LIMIT: int = 20
    RAG_DAILY_UPLOAD_ATTEMPT_LIMIT: int = 100

    FRONTEND_URL: str = "http://127.0.0.1:3000"

    # ── JWT authentication ───────────────────────────────────────────────────
    JWT_SECRET: str = "CHANGE_ME_IN_PRODUCTION_USE_A_STRONG_RANDOM_SECRET"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    JWT_AGENT_TOKEN_EXPIRE_MINUTES: int = 5

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if v == "CHANGE_ME_IN_PRODUCTION_USE_A_STRONG_RANDOM_SECRET" or len(v) < 32:
            raise ValueError(
                "JWT_SECRET must be set to a strong random secret (min 32 chars). "
                "Generate one with: openssl rand -hex 32"
            )
        return v

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


# Server-layer singleton — only import this from serving/ code.
settings = ServerSettings()

"""Server-layer configuration.

``ServerSettings`` extends ``SubstrateConfig`` with fields that only the FastAPI
server needs: JWT auth, CORS, rate limiting, observability, and feature flags.

It auto-loads ``.env`` from the current working directory (where you run
``uv run start``).  For library use, import ``SubstrateConfig`` from
``substrate.config`` instead.
"""

from __future__ import annotations

from typing import List

from pydantic import field_validator
from pydantic_settings import SettingsConfigDict

from substrate.config import SubstrateConfig


class ServerSettings(SubstrateConfig):
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

    model_config = SettingsConfigDict(
        env_file=".env",  # relative to CWD — where `uv run start` is invoked
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )


# Server-layer singleton — only import this from serving/ code.
settings = ServerSettings()

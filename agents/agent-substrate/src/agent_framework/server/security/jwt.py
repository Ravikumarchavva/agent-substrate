"""JWT token creation and verification — thin wrapper around shared auth.

Binds ``settings.JWT_SECRET`` and ``settings.JWT_ALGORITHM`` so monolith
callers can use the same simple interface (no secret/algo params needed).

The actual implementation lives in ``shared.auth.jwt``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agent_framework.configs.settings import settings
from agent_framework.shared.auth.claims import AuthClaims
from agent_framework.shared.auth import jwt as _jwt

# Backward-compat alias — monolith routes use TokenPayload
TokenPayload = AuthClaims

_SECRET = settings.JWT_SECRET
_ALG = settings.JWT_ALGORITHM


def create_access_token(
    user_id: str,
    email: str = "",
    role: str = "user",
    extra: dict[str, Any] | None = None,
) -> tuple[str, datetime]:
    return _jwt.create_access_token(
        user_id=user_id,
        email=email,
        role=role,
        secret=_SECRET,
        algorithm=_ALG,
        expire_minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
        extra=extra,
    )


def create_refresh_token(user_id: str) -> tuple[str, str, datetime]:
    return _jwt.create_refresh_token(
        user_id=user_id,
        secret=_SECRET,
        algorithm=_ALG,
        expire_days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS,
    )


def create_agent_context_token(
    user_id: str,
    thread_id: str,
    permissions: list[str] | None = None,
) -> str:
    return _jwt.create_agent_context_token(
        user_id=user_id,
        thread_id=thread_id,
        permissions=permissions,
        secret=_SECRET,
        algorithm=_ALG,
        expire_minutes=settings.JWT_AGENT_TOKEN_EXPIRE_MINUTES,
    )


def verify_token(token: str, expected_type: str | None = None) -> TokenPayload | None:
    return _jwt.verify_token(
        token,
        secret=_SECRET,
        algorithm=_ALG,
        expected_type=expected_type,
    )

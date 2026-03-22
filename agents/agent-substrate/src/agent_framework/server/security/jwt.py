"""JWT token creation and verification utilities.

Token types
-----------
access_token      Short-lived (default 60 min) bearer token for API calls.
                  Claims: sub, email, role, jti, type="access"

refresh_token     Long-lived (default 30 days) token for rotating access tokens.
                  Claims: sub, jti, type="refresh"
                  The JTI is stored in Redis so it can be revoked on rotation.

agent_context_token
                  Short-lived (default 5 min) signed token embedding
                  thread_id + permission scope.  Passed to agent tools that
                  need to call back into the API on behalf of a user session.
                  Claims: sub, thread_id, permissions, type="agent", iss="agent-framework"
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
from pydantic import BaseModel

from agent_framework.configs.settings import settings

logger = logging.getLogger(__name__)

_ALG = settings.JWT_ALGORITHM
_SECRET = settings.JWT_SECRET


# ---------------------------------------------------------------------------
# Payload model
# ---------------------------------------------------------------------------


class TokenPayload(BaseModel):
    """Decoded JWT claims attached to request.state.user."""

    sub: str  # user_id (opaque string)
    email: str = ""
    role: str = "user"  # "user" | "admin"
    jti: str = ""  # unique token ID
    type: str = "access"  # "access" | "refresh" | "agent"
    # Optional agent context fields
    thread_id: str | None = None
    permissions: list[str] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _encode(claims: dict[str, Any]) -> str:
    return jwt.encode(claims, _SECRET, algorithm=_ALG)


# ---------------------------------------------------------------------------
# Token creators
# ---------------------------------------------------------------------------


def create_access_token(
    user_id: str,
    email: str = "",
    role: str = "user",
    extra: dict[str, Any] | None = None,
) -> tuple[str, datetime]:
    """Return ``(encoded_jwt, expires_at)`` for a short-lived access token."""
    expires_at = _now() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    claims: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "role": role,
        "jti": str(uuid4()),
        "type": "access",
        "iat": _now(),
        "exp": expires_at,
        **(extra or {}),
    }
    return _encode(claims), expires_at


def create_refresh_token(user_id: str) -> tuple[str, str, datetime]:
    """Return ``(encoded_jwt, jti, expires_at)`` for a rotation-capable refresh token.

    The caller MUST store ``jti`` in Redis / DB so it can be revoked on first use.
    """
    expires_at = _now() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    jti = str(uuid4())
    claims: dict[str, Any] = {
        "sub": user_id,
        "jti": jti,
        "type": "refresh",
        "iat": _now(),
        "exp": expires_at,
    }
    return _encode(claims), jti, expires_at


def create_agent_context_token(
    user_id: str,
    thread_id: str,
    permissions: list[str] | None = None,
) -> str:
    """Issue a short-lived signed token for an agent tool callback session.

    Embed ``thread_id`` + ``permissions`` so the agent can securely call back
    into the API for a specific conversation without holding a long-lived token.
    """
    expires_at = _now() + timedelta(minutes=settings.JWT_AGENT_TOKEN_EXPIRE_MINUTES)
    claims: dict[str, Any] = {
        "sub": user_id,
        "thread_id": thread_id,
        "permissions": permissions or ["read", "write"],
        "jti": str(uuid4()),
        "type": "agent",
        "iss": "agent-framework",
        "iat": _now(),
        "exp": expires_at,
    }
    return _encode(claims)


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def verify_token(token: str, expected_type: str | None = None) -> TokenPayload | None:
    """Decode and validate a JWT.  Returns ``None`` on any failure.

    Args:
        token:         Raw JWT string (without "Bearer " prefix).
        expected_type: When provided, rejects tokens whose ``type`` claim
                       doesn't match (e.g. reject refresh tokens on API routes).
    """
    try:
        payload = jwt.decode(token, _SECRET, algorithms=[_ALG])
    except jwt.ExpiredSignatureError:
        logger.debug("JWT expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.debug("JWT invalid: %s", exc)
        return None

    if expected_type and payload.get("type") != expected_type:
        logger.debug(
            "JWT type mismatch: expected=%s, got=%s", expected_type, payload.get("type")
        )
        return None

    return TokenPayload(**payload)

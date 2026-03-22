"""JWT token utilities shared by all services.

Centralised token creation and verification. Each service imports these
instead of maintaining its own JWT logic.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Optional
from uuid import uuid4

import jwt

from agent_framework.shared.auth.claims import AuthClaims

logger = logging.getLogger(__name__)

# Defaults — overridden by each service's settings
_DEFAULT_SECRET = "CHANGE_ME_IN_PRODUCTION_USE_A_STRONG_RANDOM_SECRET"
_DEFAULT_ALG = "HS256"


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(
    user_id: str,
    email: str = "",
    role: str = "end_user",
    tenant_id: str = "default",
    workspace_id: str = "default",
    secret: str = _DEFAULT_SECRET,
    algorithm: str = _DEFAULT_ALG,
    expire_minutes: int = 60,
    extra: dict[str, Any] | None = None,
) -> tuple[str, datetime]:
    """Create a short-lived access token. Returns (jwt_str, expires_at)."""
    expires_at = _now() + timedelta(minutes=expire_minutes)
    claims: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "role": role,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "jti": str(uuid4()),
        "type": "access",
        "iat": _now(),
        "exp": expires_at,
        **(extra or {}),
    }
    return jwt.encode(claims, secret, algorithm=algorithm), expires_at


def create_refresh_token(
    user_id: str,
    secret: str = _DEFAULT_SECRET,
    algorithm: str = _DEFAULT_ALG,
    expire_days: int = 30,
) -> tuple[str, str, datetime]:
    """Create a rotation-capable refresh token. Returns (jwt_str, jti, expires_at)."""
    expires_at = _now() + timedelta(days=expire_days)
    jti = str(uuid4())
    claims: dict[str, Any] = {
        "sub": user_id,
        "jti": jti,
        "type": "refresh",
        "iat": _now(),
        "exp": expires_at,
    }
    return jwt.encode(claims, secret, algorithm=algorithm), jti, expires_at


def create_service_token(
    service_name: str,
    secret: str = _DEFAULT_SECRET,
    algorithm: str = _DEFAULT_ALG,
    expire_minutes: int = 15,
) -> str:
    """Create a short-lived service-to-service identity token."""
    expires_at = _now() + timedelta(minutes=expire_minutes)
    claims: dict[str, Any] = {
        "sub": f"service:{service_name}",
        "role": "service_runtime",
        "jti": str(uuid4()),
        "type": "service",
        "iss": "agent-framework",
        "iat": _now(),
        "exp": expires_at,
    }
    return jwt.encode(claims, secret, algorithm=algorithm)


def create_agent_context_token(
    user_id: str,
    thread_id: str,
    permissions: list[str] | None = None,
    secret: str = _DEFAULT_SECRET,
    algorithm: str = _DEFAULT_ALG,
    expire_minutes: int = 5,
) -> str:
    """Create a short-lived agent context token bound to a thread."""
    expires_at = _now() + timedelta(minutes=expire_minutes)
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
    return jwt.encode(claims, secret, algorithm=algorithm)


def verify_token(
    token: str,
    secret: str = _DEFAULT_SECRET,
    algorithm: str = _DEFAULT_ALG,
    expected_type: Optional[str] = None,
) -> Optional[AuthClaims]:
    """Decode and validate a JWT. Returns None on any failure."""
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.ExpiredSignatureError:
        logger.debug("JWT expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.debug("JWT invalid: %s", exc)
        return None

    if expected_type and payload.get("type") != expected_type:
        logger.debug(
            "JWT type mismatch: expected=%s, got=%s",
            expected_type,
            payload.get("type"),
        )
        return None

    return AuthClaims(
        sub=payload.get("sub", ""),
        email=payload.get("email", ""),
        role=payload.get("role", "end_user"),
        tenant_id=payload.get("tenant_id", "default"),
        workspace_id=payload.get("workspace_id", "default"),
        jti=payload.get("jti", ""),
        token_type=payload.get("type", "access"),
        thread_id=payload.get("thread_id"),
        permissions=payload.get("permissions"),
    )

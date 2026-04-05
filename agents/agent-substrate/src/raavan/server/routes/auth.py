"""Authentication endpoints.

POST /auth/token          Exchange a signed Next.js session token for
                          backend access + refresh tokens.
POST /auth/refresh        Rotate a refresh token.  Old token is invalidated.
POST /auth/agent-token    Issue a short-lived agent context token for a thread.
GET  /auth/me             Return the caller's decoded token payload.
POST /auth/logout         Revoke the caller's refresh token.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status

from raavan.configs.settings import settings
from raavan.server.security.deps import get_current_user
from raavan.server.security.jwt import (
    TokenPayload,
    create_access_token,
    create_agent_context_token,
    create_refresh_token,
    verify_token,
)
from raavan.shared.contracts.auth import (
    AgentTokenRequest,
    RefreshRequest,
    TokenExchangeRequest,
    TokenResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# Redis key prefix for valid refresh-token JTIs
_REFRESH_PREFIX = "rt:"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redis_key(jti: str) -> str:
    return f"{_REFRESH_PREFIX}{jti}"


async def _store_refresh_jti(request: Request, jti: str, expires_at: datetime) -> None:
    """Persist a valid refresh JTI in Redis with automatic expiry."""
    redis = getattr(request.app.state, "redis_client", None)
    if redis is None:
        return
    ttl = int((expires_at - datetime.now(UTC)).total_seconds())
    await redis.setex(_redis_key(jti), ttl, "1")


async def _revoke_refresh_jti(request: Request, jti: str) -> None:
    """Delete a refresh JTI from Redis (rotation / logout)."""
    redis = getattr(request.app.state, "redis_client", None)
    if redis is None:
        return
    await redis.delete(_redis_key(jti))


async def _is_refresh_jti_valid(request: Request, jti: str) -> bool:
    """Return True if the JTI exists in Redis (i.e. not yet rotated/revoked)."""
    redis = getattr(request.app.state, "redis_client", None)
    if redis is None:
        # Fallback: accept without Redis-side revocation check (dev mode)
        logger.warning("Redis unavailable; skipping refresh-token revocation check")
        return True
    result = await redis.get(_redis_key(jti))
    return result is not None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/token", response_model=TokenResponse)
async def exchange_token(body: TokenExchangeRequest, request: Request):
    """Exchange a Next.js session token for backend access + refresh tokens.

    The Next.js API route must sign the session as a JWT using
    ``INTERNAL_AUTH_SECRET`` (same value as ``settings.JWT_SECRET`` here) so
    the backend can verify it without a round-trip to an external auth server.

    Expected claims in the frontend token:
        sub   — stable user ID (e.g. Prisma user.id)
        email — user's email address
        role  — "user" | "admin"  (defaults to "user")
    """
    payload = verify_token(body.frontend_token, expected_type=None)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token from frontend",
        )

    access_token, expires_at = create_access_token(
        user_id=payload.sub,
        email=payload.email,
        role=payload.role,
    )
    refresh_token, jti, refresh_exp = create_refresh_token(payload.sub)
    await _store_refresh_jti(request, jti, refresh_exp)

    expires_in = int(
        timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES).total_seconds()
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(body: RefreshRequest, request: Request):
    """Rotate a refresh token — returns new access + refresh pair.

    The old refresh token JTI is immediately revoked in Redis.
    Using the same refresh token twice results in HTTP 401 (replay attack
    prevention via refresh-token rotation).
    """
    payload = verify_token(body.refresh_token, expected_type="refresh")
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    if not await _is_refresh_jti_valid(request, payload.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has already been used or revoked",
        )

    # Revoke old JTI before issuing new tokens
    await _revoke_refresh_jti(request, payload.jti)

    access_token, _ = create_access_token(user_id=payload.sub)
    refresh_token, new_jti, refresh_exp = create_refresh_token(payload.sub)
    await _store_refresh_jti(request, new_jti, refresh_exp)

    expires_in = int(
        timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES).total_seconds()
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/agent-token")
async def issue_agent_token(
    body: AgentTokenRequest,
    user: TokenPayload = Depends(get_current_user),
):
    """Issue a short-lived agent context token bound to a specific thread.

    The token is signed and carries ``thread_id`` + ``permissions`` so agent
    tools can call back into the API securely for a specific conversation
    without holding a long-lived access token.
    """
    agent_token = create_agent_context_token(
        user_id=user.sub,
        thread_id=body.thread_id,
        permissions=body.permissions,
    )
    return {
        "agent_token": agent_token,
        "token_type": "bearer",
        "expires_in": int(
            timedelta(minutes=settings.JWT_AGENT_TOKEN_EXPIRE_MINUTES).total_seconds()
        ),
        "thread_id": body.thread_id,
    }


@router.get("/me", response_model=TokenPayload)
async def get_me(user: TokenPayload = Depends(get_current_user)):
    """Return the decoded access token payload for the calling user."""
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, request: Request):
    """Revoke the supplied refresh token (logout from all devices requires
    calling this for each active refresh token or implementing a generation
    counter per user in Redis).
    """
    payload = verify_token(body.refresh_token, expected_type="refresh")
    if payload and payload.jti:
        await _revoke_refresh_jti(request, payload.jti)

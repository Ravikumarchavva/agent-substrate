"""Identity Auth Service — HTTP routes.

POST /auth/token          Exchange frontend token for access+refresh tokens
POST /auth/refresh        Rotate refresh token
POST /auth/agent-token    Issue agent context token
GET  /auth/me             Return caller's decoded token
POST /auth/logout         Revoke refresh token
GET  /auth/users/{id}     Get user by ID (service-to-service)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status

from raavan.shared.auth.claims import AuthClaims
from raavan.shared.auth.middleware import get_current_user
from raavan.shared.auth import jwt as jwt_utils
from raavan.shared.contracts.auth import (
    AgentTokenRequest,
    AgentTokenResponse,
    RefreshRequest,
    TokenExchangeRequest,
    TokenResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# Redis key prefix for valid refresh-token JTIs
_REFRESH_PREFIX = "rt:"


def _redis_key(jti: str) -> str:
    return f"{_REFRESH_PREFIX}{jti}"


async def _store_refresh_jti(request: Request, jti: str, expires_at: datetime) -> None:
    redis = getattr(request.app.state, "redis_client", None)
    if redis is None:
        return
    ttl = int((expires_at - datetime.now(UTC)).total_seconds())
    await redis.setex(_redis_key(jti), ttl, "1")


async def _revoke_refresh_jti(request: Request, jti: str) -> None:
    redis = getattr(request.app.state, "redis_client", None)
    if redis is None:
        return
    await redis.delete(_redis_key(jti))


async def _is_refresh_jti_valid(request: Request, jti: str) -> bool:
    redis = getattr(request.app.state, "redis_client", None)
    if redis is None:
        return True
    result = await redis.get(_redis_key(jti))
    return result is not None


@router.post("/token", response_model=TokenResponse)
async def exchange_token(body: TokenExchangeRequest, request: Request):
    """Exchange a frontend session token for backend access + refresh tokens."""
    from raavan.services.identity.service import exchange_frontend_token

    jwt_secret = request.app.state.jwt_secret
    db_factory = request.app.state.session_factory
    event_bus = getattr(request.app.state, "event_bus", None)

    async with db_factory() as db:
        try:
            result = await exchange_frontend_token(
                db,
                body.frontend_token,
                jwt_secret,
                event_bus,
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
            )
        await db.commit()

    await _store_refresh_jti(request, result["jti"], result["refresh_exp"])

    return TokenResponse(
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        expires_in=3600,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(body: RefreshRequest, request: Request):
    """Rotate a refresh token."""
    jwt_secret = request.app.state.jwt_secret
    payload = jwt_utils.verify_token(
        body.refresh_token,
        secret=jwt_secret,
        expected_type="refresh",
    )
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    if not await _is_refresh_jti_valid(request, payload.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token already used or revoked",
        )

    await _revoke_refresh_jti(request, payload.jti)

    access_token, _ = jwt_utils.create_access_token(
        user_id=payload.sub,
        secret=jwt_secret,
    )
    refresh_token, new_jti, refresh_exp = jwt_utils.create_refresh_token(
        user_id=payload.sub,
        secret=jwt_secret,
    )
    await _store_refresh_jti(request, new_jti, refresh_exp)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=3600,
    )


@router.post("/agent-token")
async def issue_agent_token(
    body: AgentTokenRequest,
    user: AuthClaims = Depends(get_current_user),
    request: Request | None = None,
):
    """Issue a short-lived agent context token bound to a thread."""
    if request is None:
        raise HTTPException(status_code=500, detail="Request context unavailable")

    jwt_secret = request.app.state.jwt_secret
    agent_token = jwt_utils.create_agent_context_token(
        user_id=user.sub,
        thread_id=body.thread_id,
        permissions=body.permissions,
        secret=jwt_secret,
    )
    return AgentTokenResponse(
        agent_token=agent_token,
        expires_in=300,
        thread_id=body.thread_id,
    )


@router.get("/me")
async def get_me(user: AuthClaims = Depends(get_current_user)):
    """Return the decoded access token payload."""
    return user.model_dump()


@router.post("/logout", status_code=204)
async def logout(body: RefreshRequest, request: Request):
    """Revoke a refresh token."""
    jwt_secret = request.app.state.jwt_secret
    payload = jwt_utils.verify_token(
        body.refresh_token,
        secret=jwt_secret,
        expected_type="refresh",
    )
    if payload and payload.jti:
        await _revoke_refresh_jti(request, payload.jti)

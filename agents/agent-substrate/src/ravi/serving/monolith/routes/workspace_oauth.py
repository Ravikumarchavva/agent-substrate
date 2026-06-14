"""Google Workspace OAuth token bridge — Redis-backed.

Accepts workspace tokens pushed from the Next.js frontend, persists them
in Redis, and exposes helpers for GoogleWorkspaceTool to read them.
Tokens survive backend restarts.
"""

from __future__ import annotations
from ravi.logger import setup_logging

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ravi.serving.monolith.security.deps import AuthClaims, get_current_user

logger = setup_logging()

router = APIRouter(prefix="/auth/workspace", tags=["workspace-oauth"])
DEFAULT_SESSION_ID = "default_user"
_REDIS_KEY_PREFIX = "workspace_token"


def _redis_key(session_id: str = DEFAULT_SESSION_ID) -> str:
    return f"{_REDIS_KEY_PREFIX}:{session_id}"


def _get_redis(request: Request) -> Any:
    """Return the shared ``redis.asyncio`` client from ``app.state``."""
    return request.app.state.redis_client


async def store_workspace_tokens_async(
    redis: Any,
    *,
    access_token: str,
    refresh_token: str | None = None,
    expires_in: int = 3600,
    session_id: str = DEFAULT_SESSION_ID,
) -> None:
    """Persist workspace tokens in Redis with a TTL matching *expires_in*."""
    try:
        expires_in_seconds = max(int(expires_in), 0)
    except (TypeError, ValueError):
        expires_in_seconds = 3600

    payload = json.dumps(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": expires_in_seconds,
            "expires_at": int(time.time()) + expires_in_seconds,
        }
    )
    # Keep it alive for the token lifetime + a small grace period so
    # the UI can refresh before it disappears.
    ttl = max(expires_in_seconds, 60) + 300
    await redis.set(_redis_key(session_id), payload, ex=ttl)


async def _workspace_tokens_are_usable(
    redis: Any,
    tokens: dict[str, Any],
    session_id: str,
) -> bool:
    """Return True when the stored token payload is present and unexpired."""
    expires_at = tokens.get("expires_at")
    if not isinstance(expires_at, (int, float)):
        logger.info(
            "Google Workspace token missing expires_at; clearing stale Redis entry"
        )
        await clear_workspace_tokens_async(redis, session_id)
        return False

    if expires_at <= time.time():
        logger.info("Google Workspace token expired; clearing stale Redis entry")
        await clear_workspace_tokens_async(redis, session_id)
        return False

    return True


async def get_workspace_tokens_async(
    redis: Any,
    session_id: str = DEFAULT_SESSION_ID,
) -> dict[str, Any] | None:
    """Return usable workspace tokens from Redis, or ``None`` if absent/expired."""
    raw = await redis.get(_redis_key(session_id))
    if not raw:
        return None
    try:
        tokens = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(tokens, dict):
        return None
    if not await _workspace_tokens_are_usable(redis, tokens, session_id):
        return None
    return tokens  # type: ignore[no-any-return]


async def get_workspace_access_token_async(
    redis: Any,
    session_id: str = DEFAULT_SESSION_ID,
) -> str | None:
    """Return the current usable workspace access token from Redis."""
    tokens = await get_workspace_tokens_async(redis, session_id)
    if not tokens:
        return None
    token = tokens.get("access_token")
    return token if isinstance(token, str) and token else None


async def clear_workspace_tokens_async(
    redis: Any,
    session_id: str = DEFAULT_SESSION_ID,
) -> None:
    """Remove workspace tokens from Redis."""
    await redis.delete(_redis_key(session_id))


# ── Routes ───────────────────────────────────────────────────────────────────


@router.post("/set-token")
async def set_workspace_token(
    request: Request,
    current_user: AuthClaims = Depends(get_current_user),
) -> JSONResponse:
    """Accept a Google Workspace OAuth token pushed from the Next.js frontend."""
    body = await request.json()
    redis = _get_redis(request)
    await store_workspace_tokens_async(
        redis,
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token"),
        expires_in=body.get("expires_in", 3600),
        session_id=current_user.sub,
    )
    logger.info("Google Workspace OAuth token stored in Redis")
    return JSONResponse({"status": "ok"})


@router.get("/token")
async def get_workspace_token(
    request: Request,
    current_user: AuthClaims = Depends(get_current_user),
) -> JSONResponse:
    """Return the stored Google Workspace access token."""
    redis = _get_redis(request)
    access_token = await get_workspace_access_token_async(
        redis, session_id=current_user.sub
    )
    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="Google Workspace not connected. Please connect in Settings > Apps.",
        )
    return JSONResponse({"access_token": access_token})


@router.delete("/token")
async def clear_workspace_token(
    request: Request,
    current_user: AuthClaims = Depends(get_current_user),
) -> JSONResponse:
    """Clear the stored workspace token (disconnect)."""
    redis = _get_redis(request)
    await clear_workspace_tokens_async(redis, session_id=current_user.sub)
    logger.info("Google Workspace token cleared from Redis")
    return JSONResponse({"status": "ok"})

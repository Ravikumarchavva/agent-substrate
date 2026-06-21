"""Internal connector token cache — engine-side Redis store for OAuth tokens.

ravi (SaaS) owns OAuth initiation, callbacks, and credential storage (Prisma).
After a successful OAuth callback, ravi pushes the token here so the engine's
tools can access it without a round-trip back to substrate

Redis key pattern: ``connector:{type}:{project_id}``
(e.g. ``connector:spotify:proj_abc123``)

These endpoints are internal — they require a valid engine JWT and are not
exposed to the end user.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from substrate.logger import setup_logging
from substrate.serving.monolith.security.deps import AuthClaims, get_current_user

logger = setup_logging()

router = APIRouter(prefix="/internal/connector", tags=["connector-tokens"])


def _redis(request: Request) -> Any:
    return request.app.state.redis_client


def _redis_key(connector_type: str, project_id: str) -> str:
    return f"connector:{connector_type}:{project_id}"


class TokenPushBody(BaseModel):
    project_id: str
    access_token: str
    refresh_token: str | None = None
    expires_at: int


class TokenDeleteBody(BaseModel):
    project_id: str


@router.post("/{connector_type}/token")
async def push_connector_token(
    connector_type: str,
    body: TokenPushBody,
    request: Request,
    _: AuthClaims = Depends(get_current_user),
) -> JSONResponse:
    """Store an OAuth token in Redis under a project-scoped key.

    Called by ravi after a successful OAuth callback.
    TTL is set to (expires_at - now) + 5 min grace, minimum 60 s.
    """
    import time

    key = _redis_key(connector_type, body.project_id)
    ttl = max(body.expires_at - int(time.time()) + 300, 60)
    await _redis(request).set(key, body.model_dump_json(), ex=ttl)
    logger.info("Stored %s token for project %s", connector_type, body.project_id)
    return JSONResponse({"status": "ok"})


@router.delete("/{connector_type}/token")
async def delete_connector_token(
    connector_type: str,
    body: TokenDeleteBody,
    request: Request,
    _: AuthClaims = Depends(get_current_user),
) -> JSONResponse:
    """Remove an OAuth token from Redis (called by ravi on disconnect)."""
    key = _redis_key(connector_type, body.project_id)
    await _redis(request).delete(key)
    logger.info("Deleted %s token for project %s", connector_type, body.project_id)
    return JSONResponse({"status": "ok"})

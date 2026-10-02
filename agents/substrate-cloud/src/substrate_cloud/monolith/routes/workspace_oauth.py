"""Google Workspace connector token cache.

The frontend owns the OAuth dance (code exchange, refresh) against Google
and Prisma is its durable store; this is only a short-lived Redis cache so
repeated backend calls that need the token (e.g. a `google_workspace` tool)
don't round-trip through the frontend on every use. If this cache is empty
(cold start, TTL expired, Redis unavailable), the frontend's
``/api/workspace/token`` route falls back to Prisma and re-populates this
cache via ``POST /set-token`` — see substrate-ui's
``src/app/api/workspace/token/route.ts``.

Routes:
  GET    /auth/workspace/token       Return the cached access token, if any.
  POST   /auth/workspace/set-token   Cache a token (called after the
                                      frontend obtains/refreshes one).
  DELETE /auth/workspace/token       Clear the cached token (disconnect).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims

router = APIRouter(prefix="/auth/workspace", tags=["auth"])

_REDIS_PREFIX = "workspace_token:"


class SetWorkspaceTokenRequest(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_in: int = 3600


class WorkspaceTokenResponse(BaseModel):
    access_token: str


def _redis_key(user_id: str) -> str:
    return f"{_REDIS_PREFIX}{user_id}"


@router.get("/token", response_model=WorkspaceTokenResponse)
async def get_workspace_token(
    request: Request,
    claims: AuthClaims = Depends(get_current_user),
) -> WorkspaceTokenResponse:
    redis = getattr(request.app.state, "redis_client", None)
    token = await redis.get(_redis_key(claims.sub)) if redis is not None else None
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No cached Google Workspace token for this user",
        )
    return WorkspaceTokenResponse(
        access_token=token if isinstance(token, str) else token.decode()
    )


@router.post("/set-token", status_code=status.HTTP_204_NO_CONTENT)
async def set_workspace_token(
    body: SetWorkspaceTokenRequest,
    request: Request,
    claims: AuthClaims = Depends(get_current_user),
) -> None:
    redis = getattr(request.app.state, "redis_client", None)
    if redis is None:
        # No cache available — the frontend's own Prisma fallback remains
        # the source of truth, so this is a soft no-op, not an error.
        return
    await redis.setex(
        _redis_key(claims.sub), max(1, body.expires_in), body.access_token
    )


@router.delete("/token", status_code=status.HTTP_204_NO_CONTENT)
async def clear_workspace_token(
    request: Request,
    claims: AuthClaims = Depends(get_current_user),
) -> None:
    redis = getattr(request.app.state, "redis_client", None)
    if redis is not None:
        await redis.delete(_redis_key(claims.sub))

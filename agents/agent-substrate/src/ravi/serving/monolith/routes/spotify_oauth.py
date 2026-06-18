"""Spotify token relay routes — engine-side half of the Spotify integration.

The OAuth flow (login, callback, CSRF) lives in ravi-ui (Next.js).
After the user authenticates there, Next.js pushes the access/refresh tokens
here via POST /auth/spotify/set-token so the engine's MCP app can use them.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ravi.config import settings
from ravi.logger import setup_logging

logger = setup_logging()

router = APIRouter(prefix="/auth/spotify", tags=["spotify-oauth"])

# Single-user key — ravi-ui is a per-user single-tenant deployment.
_TOKEN_KEY = "spotify:token:default"


def _redis(request: Request) -> Any:
    return request.app.state.redis_client


async def _get_tokens(request: Request) -> Dict[str, Any] | None:
    raw = await _redis(request).get(_TOKEN_KEY)
    if not raw:
        return None
    return json.loads(raw)


async def _save_tokens(request: Request, tokens: Dict[str, Any]) -> None:
    await _redis(request).set(_TOKEN_KEY, json.dumps(tokens), ex=86400)


@router.post("/set-token")
async def set_access_token(request: Request) -> JSONResponse:
    """Accept and store an OAuth token pushed from the Next.js frontend after OAuth."""
    body = await request.json()
    token_payload = json.dumps(
        {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token"),
            "expires_in": body.get("expires_in", 3600),
        }
    )
    await _redis(request).set(_TOKEN_KEY, token_payload, ex=86400)
    logger.info("Spotify token stored via Next.js push")
    return JSONResponse({"status": "ok"})


@router.get("/token")
async def get_access_token(request: Request) -> JSONResponse:
    """Return the stored access token (used by the MCP app and AppPanel)."""
    tokens = await _get_tokens(request)
    if not tokens:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Connect Spotify in the Settings panel.",
        )
    return JSONResponse(
        {"access_token": tokens["access_token"], "expires_in": tokens.get("expires_in", 3600)}
    )


@router.post("/refresh")
async def refresh_token(request: Request) -> JSONResponse:
    """Refresh the access token using the stored refresh token."""
    tokens = await _get_tokens(request)
    if not tokens or not tokens.get("refresh_token"):
        raise HTTPException(status_code=401, detail="No refresh token. Connect Spotify again.")

    from ravi.integrations.spotify.auth import SpotifyAuthService

    auth_service = SpotifyAuthService(
        client_id=settings.SPOTIFY_CLIENT_ID,
        client_secret=settings.SPOTIFY_CLIENT_SECRET,
        redirect_uri="",
    )
    try:
        new_data = await auth_service.refresh_access_token(tokens["refresh_token"])
        tokens.update(
            {
                "access_token": new_data["access_token"],
                "expires_in": new_data.get("expires_in", 3600),
            }
        )
        await _save_tokens(request, tokens)
        logger.info("Refreshed Spotify access token")
        return JSONResponse(
            {
                "access_token": new_data["access_token"],
                "expires_in": new_data.get("expires_in", 3600),
            }
        )
    except Exception as e:
        logger.error("Failed to refresh Spotify token: %s", e)
        raise HTTPException(status_code=500, detail="Token refresh failed")


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    """Clear the stored Spotify token."""
    await _redis(request).delete(_TOKEN_KEY)
    logger.info("Spotify token cleared")
    return JSONResponse({"message": "Logged out"})


def _spotify_service(tokens: Dict[str, Any] | None) -> Any:
    from ravi.integrations.spotify.client import SpotifyService

    if not tokens or not tokens.get("access_token"):
        return None
    return SpotifyService(
        client_id=settings.SPOTIFY_CLIENT_ID,
        client_secret=settings.SPOTIFY_CLIENT_SECRET,
        oauth_token=tokens["access_token"],
    )


@router.get("/liked-songs")
async def get_liked_songs(
    request: Request,
    limit: int = Query(50, ge=1, le=50),
    offset: int = Query(0, ge=0),
    market: Optional[str] = Query(None),
) -> JSONResponse:
    svc = _spotify_service(await _get_tokens(request))
    if svc is None:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify")
    try:
        return JSONResponse(await svc.get_liked_songs(limit=limit, offset=offset, market=market))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/playlists")
async def get_playlists(
    request: Request,
    limit: int = Query(50, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> JSONResponse:
    svc = _spotify_service(await _get_tokens(request))
    if svc is None:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify")
    try:
        return JSONResponse(await svc.get_playlists(limit=limit, offset=offset))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/playlists/{playlist_id}/tracks")
async def get_playlist_tracks(
    request: Request,
    playlist_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    market: Optional[str] = Query(None),
) -> JSONResponse:
    svc = _spotify_service(await _get_tokens(request))
    if svc is None:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify")
    try:
        return JSONResponse(
            await svc.get_playlist_tracks(
                playlist_id=playlist_id, limit=limit, offset=offset, market=market
            )
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

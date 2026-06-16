"""Spotify OAuth authentication routes for Web Playback SDK."""

from __future__ import annotations
from ravi.logger import setup_logging

import html
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ravi.config import settings
from ravi.integrations.spotify.auth import SpotifyAuthService
from ravi.serving.monolith.security.deps import AuthClaims, get_current_user

logger = setup_logging()

router = APIRouter(prefix="/auth/spotify", tags=["spotify-oauth"])

_FRONTEND_ORIGIN = settings.FRONTEND_URL

# Redis key patterns
_TOKEN_KEY = "spotify:token:{user_id}"  # EX 86400 (24 h)
_STATE_KEY = "spotify:state:{state}"  # EX 600   (10 min CSRF window)


def _token_key(user_id: str) -> str:
    return _TOKEN_KEY.format(user_id=user_id)


def _state_key(state: str) -> str:
    return _STATE_KEY.format(state=state)


def _redis(request: Request):
    return request.app.state.redis_client


def get_auth_service() -> SpotifyAuthService:
    redirect_uri = (
        settings.SPOTIFY_REDIRECT_URI or "http://localhost:8001/auth/spotify/callback"
    )
    return SpotifyAuthService(
        client_id=settings.SPOTIFY_CLIENT_ID,
        client_secret=settings.SPOTIFY_CLIENT_SECRET,
        redirect_uri=redirect_uri,
    )


@router.get("/login")
async def spotify_login(
    request: Request,
    current_user: AuthClaims = Depends(get_current_user),
):
    """Redirect user to Spotify OAuth authorization page."""
    auth_service = get_auth_service()
    auth_url, state = auth_service.get_authorization_url()

    # Store state → user_id in Redis (10-minute CSRF window)
    await _redis(request).set(_state_key(state), current_user.sub, ex=600)

    logger.info("Redirecting user %s to Spotify OAuth login", current_user.sub)
    return RedirectResponse(auth_url)


@router.get("/callback")
async def spotify_callback(
    request: Request,
    code: str = Query(..., description="Authorization code from Spotify"),
    state: str = Query(..., description="State parameter for CSRF protection"),
    error: Optional[str] = Query(None, description="Error if user declined"),
):
    """Handle OAuth callback from Spotify."""
    if error:
        safe_error = html.escape(error)
        error_json = json.dumps(error)
        origin_json = json.dumps(_FRONTEND_ORIGIN)
        logger.error("Spotify OAuth error: %s", safe_error)
        return HTMLResponse(
            content=f"""
            <html>
                <body>
                    <h1>Spotify Authentication Failed</h1>
                    <p>Error: {safe_error}</p>
                    <script>
                        window.opener?.postMessage({{
                            type: 'spotify_auth_error',
                            error: {error_json}
                        }}, {origin_json});
                        setTimeout(() => window.close(), 3000);
                    </script>
                </body>
            </html>
            """,
            status_code=400,
        )

    redis = _redis(request)

    # Validate state (consume atomically — prevents replay)
    user_id: str | None = await redis.getdel(_state_key(state))
    if not user_id:
        logger.error("Invalid or expired OAuth state parameter")
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    auth_service = get_auth_service()
    try:
        token_data = await auth_service.exchange_code_for_token(code)

        token_payload = json.dumps(
            {
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token"),
                "expires_in": token_data.get("expires_in", 3600),
                "scope": token_data.get("scope", ""),
            }
        )
        await redis.set(_token_key(user_id), token_payload, ex=86400)

        logger.info("Stored Spotify tokens for user %s", user_id)

        safe_tokens = json.dumps(
            {
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token", ""),
                "expires_in": token_data.get("expires_in", 3600),
                "scope": token_data.get("scope", ""),
            }
        )
        origin_json = json.dumps(_FRONTEND_ORIGIN)

        return HTMLResponse(
            content=f"""
            <html>
                <head><title>Spotify Authentication Success</title></head>
                <body>
                    <h1>Connected to Spotify!</h1>
                    <p>You can close this window...</p>
                    <script>
                        if (window.opener) {{
                            window.opener.postMessage({{
                                type: 'spotify_auth_success',
                                tokens: {safe_tokens}
                            }}, {origin_json});
                        }}
                        setTimeout(() => window.close(), 2000);
                    </script>
                </body>
            </html>
            """,
            status_code=200,
        )

    except Exception as e:
        logger.error("Failed to exchange OAuth code: %s", e)
        raise HTTPException(status_code=500, detail="Token exchange failed")


@router.post("/set-token")
async def set_access_token_from_frontend(
    request: Request,
    current_user: AuthClaims = Depends(get_current_user),
):
    """Accept and store an OAuth token pushed from the frontend after user-facing OAuth."""
    body = await request.json()
    token_payload = json.dumps(
        {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token"),
            "expires_in": body.get("expires_in", 3600),
        }
    )
    await _redis(request).set(_token_key(current_user.sub), token_payload, ex=86400)
    logger.info(
        "Spotify OAuth token stored from frontend push for user %s", current_user.sub
    )
    return JSONResponse({"status": "ok"})


async def _get_tokens(request: Request, user_id: str) -> Dict[str, Any] | None:
    raw = await _redis(request).get(_token_key(user_id))
    if not raw:
        return None
    return json.loads(raw)


async def _save_tokens(request: Request, user_id: str, tokens: Dict[str, Any]) -> None:
    await _redis(request).set(_token_key(user_id), json.dumps(tokens), ex=86400)


@router.get("/token")
async def get_access_token(
    request: Request,
    current_user: AuthClaims = Depends(get_current_user),
):
    """Get current user's Spotify access token."""
    tokens = await _get_tokens(request, current_user.sub)
    if not tokens:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Please log in with Spotify first.",
        )
    return JSONResponse(
        {"access_token": tokens["access_token"], "expires_in": tokens["expires_in"]}
    )


@router.post("/refresh")
async def refresh_token(
    request: Request,
    current_user: AuthClaims = Depends(get_current_user),
):
    """Refresh the access token using refresh token."""
    tokens = await _get_tokens(request, current_user.sub)
    if not tokens or not tokens.get("refresh_token"):
        raise HTTPException(
            status_code=401, detail="No refresh token available. Please log in again."
        )

    auth_service = get_auth_service()
    try:
        new_token_data = await auth_service.refresh_access_token(
            tokens["refresh_token"]
        )
        tokens.update(
            {
                "access_token": new_token_data["access_token"],
                "expires_in": new_token_data.get("expires_in", 3600),
            }
        )
        await _save_tokens(request, current_user.sub, tokens)
        logger.info("Refreshed Spotify access token for user %s", current_user.sub)
        return JSONResponse(
            {
                "access_token": new_token_data["access_token"],
                "expires_in": new_token_data.get("expires_in", 3600),
            }
        )
    except Exception as e:
        logger.error("Failed to refresh token: %s", e)
        raise HTTPException(status_code=500, detail="Token refresh failed")


@router.post("/logout")
async def logout(
    request: Request,
    current_user: AuthClaims = Depends(get_current_user),
):
    """Log out user and clear tokens."""
    await _redis(request).delete(_token_key(current_user.sub))
    logger.info("Logged out Spotify session for user %s", current_user.sub)
    return JSONResponse({"message": "Logged out successfully"})


@router.post("/restore")
async def restore_tokens(
    request: Request,
    current_user: AuthClaims = Depends(get_current_user),
):
    """Restore OAuth tokens from client-side localStorage."""
    body = await request.json()
    user_id = current_user.sub

    access_token_val = body.get("access_token")
    refresh_token_val = body.get("refresh_token")

    if not access_token_val and not refresh_token_val:
        raise HTTPException(status_code=400, detail="No tokens provided")

    existing = await _get_tokens(request, user_id)
    if existing and existing.get("access_token"):
        return JSONResponse(
            {
                "access_token": existing["access_token"],
                "expires_in": existing.get("expires_in", 3600),
                "status": "already_active",
            }
        )

    if refresh_token_val:
        try:
            auth_service = get_auth_service()
            new_data = await auth_service.refresh_access_token(refresh_token_val)
            await _save_tokens(
                request,
                user_id,
                {
                    "access_token": new_data["access_token"],
                    "refresh_token": refresh_token_val,
                    "expires_in": new_data.get("expires_in", 3600),
                    "scope": body.get("scope", ""),
                },
            )
            logger.info("Restored Spotify tokens (refreshed) for user %s", user_id)
            return JSONResponse(
                {
                    "access_token": new_data["access_token"],
                    "expires_in": new_data.get("expires_in", 3600),
                    "status": "refreshed",
                }
            )
        except Exception as e:
            logger.warning("Could not refresh during restore: %s", e)

    if access_token_val:
        await _save_tokens(
            request,
            user_id,
            {
                "access_token": access_token_val,
                "refresh_token": refresh_token_val,
                "expires_in": body.get("expires_in", 3600),
                "scope": body.get("scope", ""),
            },
        )
        logger.info("Restored Spotify tokens (as-is) for user %s", user_id)
        return JSONResponse(
            {
                "access_token": access_token_val,
                "expires_in": body.get("expires_in", 3600),
                "status": "stored",
            }
        )

    raise HTTPException(status_code=400, detail="Could not restore tokens")


def _get_oauth_service_with_token_sync(tokens: Dict[str, Any] | None):
    """Return a SpotifyService initialised with the OAuth token, or None."""
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
    current_user: AuthClaims = Depends(get_current_user),
):
    """Fetch the current user's liked (saved) tracks."""
    svc = _get_oauth_service_with_token_sync(
        await _get_tokens(request, current_user.sub)
    )
    if svc is None:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify")
    try:
        result = await svc.get_liked_songs(limit=limit, offset=offset, market=market)
        return JSONResponse(result)
    except Exception as e:
        logger.error("Failed to fetch liked songs: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to fetch liked songs: {e}")


@router.get("/playlists")
async def get_playlists(
    request: Request,
    limit: int = Query(50, ge=1, le=50),
    offset: int = Query(0, ge=0),
    current_user: AuthClaims = Depends(get_current_user),
):
    """Fetch the current user's playlists."""
    svc = _get_oauth_service_with_token_sync(
        await _get_tokens(request, current_user.sub)
    )
    if svc is None:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify")
    try:
        result = await svc.get_playlists(limit=limit, offset=offset)
        return JSONResponse(result)
    except Exception as e:
        logger.error("Failed to fetch playlists: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to fetch playlists: {e}")


@router.get("/playlists/{playlist_id}/tracks")
async def get_playlist_tracks(
    request: Request,
    playlist_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    market: Optional[str] = Query(None),
    current_user: AuthClaims = Depends(get_current_user),
):
    """Fetch tracks for a specific playlist."""
    svc = _get_oauth_service_with_token_sync(
        await _get_tokens(request, current_user.sub)
    )
    if svc is None:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify")
    try:
        result = await svc.get_playlist_tracks(
            playlist_id=playlist_id, limit=limit, offset=offset, market=market
        )
        return JSONResponse(result)
    except Exception as e:
        logger.error("Failed to fetch playlist tracks for %s: %s", playlist_id, e)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch playlist tracks: {e}"
        )

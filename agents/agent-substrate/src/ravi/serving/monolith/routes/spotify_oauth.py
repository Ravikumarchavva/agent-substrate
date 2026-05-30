"""Spotify OAuth authentication routes for Web Playback SDK."""

from __future__ import annotations
from ravi.logger import setup_logging

import html
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ravi.config import settings
from ravi.adapters.spotify.auth import SpotifyAuthService
from ravi.serving.monolith.security.deps import TokenPayload, get_current_user

logger = setup_logging()

router = APIRouter(prefix="/auth/spotify", tags=["spotify-oauth"])

# In-memory token storage (use Redis/database in production)
_user_tokens: Dict[str, Dict[str, Any]] = {}

# In-memory CSRF state store keyed by state value (use Redis in production)
_oauth_states: Dict[str, bool] = {}

# Target origin for postMessage — prevents leaking tokens to other origins
_FRONTEND_ORIGIN = settings.FRONTEND_URL


def get_auth_service() -> SpotifyAuthService:
    """Get Spotify OAuth service instance."""
    redirect_uri = (
        settings.SPOTIFY_REDIRECT_URI or "http://localhost:8001/auth/spotify/callback"
    )

    return SpotifyAuthService(
        client_id=settings.SPOTIFY_CLIENT_ID,
        client_secret=settings.SPOTIFY_CLIENT_SECRET,
        redirect_uri=redirect_uri,
    )


@router.get("/login")
async def spotify_login(request: Request):
    """Redirect user to Spotify OAuth authorization page.

    Opens Spotify login where user grants permissions for:
    - Streaming (Web Playback SDK)
    - Read email & private info
    - Control playback
    """
    auth_service = get_auth_service()
    auth_url, state = auth_service.get_authorization_url()

    # Store state for CSRF validation (per-request, not shared across users)
    _oauth_states[state] = True

    logger.info("Redirecting to Spotify OAuth login")
    return RedirectResponse(auth_url)


@router.get("/callback")
async def spotify_callback(
    request: Request,
    code: str = Query(..., description="Authorization code from Spotify"),
    state: str = Query(..., description="State parameter for CSRF protection"),
    error: Optional[str] = Query(None, description="Error if user declined"),
):
    """Handle OAuth callback from Spotify.

    Exchanges authorization code for access + refresh tokens and closes popup.
    """
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

    auth_service = get_auth_service()

    # Validate state to prevent CSRF (consume the state so it can't be replayed)
    if state not in _oauth_states:
        logger.error("Invalid OAuth state parameter")
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    del _oauth_states[state]

    try:
        # Exchange code for tokens
        token_data = await auth_service.exchange_code_for_token(code)

        # Store tokens (use session ID or user ID in production)
        session_id = "default_user"  # TODO: Use actual session management
        _user_tokens[session_id] = {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "expires_in": token_data.get("expires_in", 3600),
            "scope": token_data.get("scope", ""),
        }

        logger.info("Successfully stored Spotify tokens for session: %s", session_id)

        # Serialize token data safely using json.dumps to prevent XSS
        safe_tokens = json.dumps(
            {
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token", ""),
                "expires_in": token_data.get("expires_in", 3600),
                "scope": token_data.get("scope", ""),
            }
        )
        origin_json = json.dumps(_FRONTEND_ORIGIN)

        # Return HTML that sends tokens to parent window and closes popup
        return HTMLResponse(
            content=f"""
            <html>
                <head>
                    <title>Spotify Authentication Success</title>
                </head>
                <body>
                    <h1>Connected to Spotify!</h1>
                    <p>You can close this window...</p>
                    <script>
                        // Send tokens to parent window (opener)
                        if (window.opener) {{
                            window.opener.postMessage({{
                                type: 'spotify_auth_success',
                                tokens: {safe_tokens}
                            }}, {origin_json});
                        }}
                        
                        // Auto-close after 2 seconds
                        setTimeout(() => {{
                            window.close();
                        }}, 2000);
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
    current_user: TokenPayload = Depends(get_current_user),
):
    """Accept and store an OAuth token pushed from the frontend after user-facing OAuth.

    Called by the Next.js callback route so the backend always has the latest user
    OAuth token without requiring the user to go through the backend OAuth flow.
    """
    session_id = current_user.sub
    body = await request.json()
    _user_tokens[session_id] = {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token"),
        "expires_in": body.get("expires_in", 3600),
    }
    logger.info("Spotify OAuth token stored from frontend push")
    return JSONResponse({"status": "ok"})


@router.get("/token")
async def get_access_token(
    current_user: TokenPayload = Depends(get_current_user),
):
    """Get current user's Spotify access token.

    Returns:
        JSON with access_token for Web Playback SDK initialization
    """
    session_id = current_user.sub

    tokens = _user_tokens.get(session_id)
    if not tokens:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Please log in with Spotify first.",
        )

    return JSONResponse(
        {
            "access_token": tokens["access_token"],
            "expires_in": tokens["expires_in"],
        }
    )


@router.post("/refresh")
async def refresh_token(
    current_user: TokenPayload = Depends(get_current_user),
):
    """Refresh the access token using refresh token.

    Called automatically when access token expires.
    """
    session_id = current_user.sub

    tokens = _user_tokens.get(session_id)
    if not tokens or not tokens.get("refresh_token"):
        raise HTTPException(
            status_code=401, detail="No refresh token available. Please log in again."
        )

    auth_service = get_auth_service()

    try:
        new_token_data = await auth_service.refresh_access_token(
            tokens["refresh_token"]
        )

        # Update stored tokens
        _user_tokens[session_id].update(
            {
                "access_token": new_token_data["access_token"],
                "expires_in": new_token_data.get("expires_in", 3600),
            }
        )

        logger.info("Refreshed access token for session: %s", session_id)

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
    current_user: TokenPayload = Depends(get_current_user),
):
    """Log out user and clear tokens."""
    session_id = current_user.sub

    if session_id in _user_tokens:
        del _user_tokens[session_id]
        logger.info("Logged out session: %s", session_id)

    return JSONResponse({"message": "Logged out successfully"})


@router.post("/restore")
async def restore_tokens(
    request: Request,
    current_user: TokenPayload = Depends(get_current_user),
):
    """Restore OAuth tokens from client-side localStorage.

    Called when the Spotify player iframe loads and has tokens saved
    in localStorage that the server may have lost (e.g., after restart).
    Uses the refresh_token to obtain a fresh access_token.
    """
    body = await request.json()
    session_id = current_user.sub

    access_token_val = body.get("access_token")
    refresh_token_val = body.get("refresh_token")

    if not access_token_val and not refresh_token_val:
        raise HTTPException(status_code=400, detail="No tokens provided")

    # If we already have tokens in memory, just return them
    existing = _user_tokens.get(session_id)
    if existing and existing.get("access_token"):
        return JSONResponse(
            {
                "access_token": existing["access_token"],
                "expires_in": existing.get("expires_in", 3600),
                "status": "already_active",
            }
        )

    # Try to refresh using the provided refresh token
    if refresh_token_val:
        try:
            auth_service = get_auth_service()
            new_data = await auth_service.refresh_access_token(refresh_token_val)
            _user_tokens[session_id] = {
                "access_token": new_data["access_token"],
                "refresh_token": refresh_token_val,
                "expires_in": new_data.get("expires_in", 3600),
                "scope": body.get("scope", ""),
            }
            logger.info("Restored Spotify tokens from client localStorage (refreshed)")
            return JSONResponse(
                {
                    "access_token": new_data["access_token"],
                    "expires_in": new_data.get("expires_in", 3600),
                    "status": "refreshed",
                }
            )
        except Exception as e:
            logger.warning("Could not refresh during restore: %s", e)

    # Fall back to storing the provided access token as-is
    if access_token_val:
        _user_tokens[session_id] = {
            "access_token": access_token_val,
            "refresh_token": refresh_token_val,
            "expires_in": body.get("expires_in", 3600),
            "scope": body.get("scope", ""),
        }
        logger.info("Restored Spotify tokens from client localStorage (stored as-is)")
        return JSONResponse(
            {
                "access_token": access_token_val,
                "expires_in": body.get("expires_in", 3600),
                "status": "stored",
            }
        )

    raise HTTPException(status_code=400, detail="Could not restore tokens")


def _get_oauth_service_with_token(session_id: str = "default_user"):
    """Return a SpotifyService initialised with the current user OAuth token, or None."""
    from ravi.adapters.spotify.client import SpotifyService

    tokens = _user_tokens.get(session_id)
    if not tokens or not tokens.get("access_token"):
        return None

    return SpotifyService(
        client_id=settings.SPOTIFY_CLIENT_ID,
        client_secret=settings.SPOTIFY_CLIENT_SECRET,
        oauth_token=tokens["access_token"],
    )


@router.get("/liked-songs")
async def get_liked_songs(
    limit: int = Query(50, ge=1, le=50),
    offset: int = Query(0, ge=0),
    market: Optional[str] = Query(None),
    current_user: TokenPayload = Depends(get_current_user),
):
    """Fetch the current user's liked (saved) tracks.

    Requires user-library-read scope in the stored OAuth token.
    """
    svc = _get_oauth_service_with_token(current_user.sub)
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
    limit: int = Query(50, ge=1, le=50),
    offset: int = Query(0, ge=0),
    current_user: TokenPayload = Depends(get_current_user),
):
    """Fetch the current user's playlists.

    Requires playlist-read-private scope in the stored OAuth token.
    """
    svc = _get_oauth_service_with_token(current_user.sub)
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
    playlist_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    market: Optional[str] = Query(None),
    current_user: TokenPayload = Depends(get_current_user),
):
    """Fetch tracks for a specific playlist."""
    svc = _get_oauth_service_with_token(current_user.sub)
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

"""Spotify OAuth authentication routes for Web Playback SDK."""

from __future__ import annotations

import html
import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from agent_framework.configs.settings import settings
from agent_framework.integrations.spotify.auth import SpotifyAuthService

logger = logging.getLogger(__name__)

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


@router.get("/token")
async def get_access_token(request: Request):
    """Get current user's Spotify access token.

    Returns:
        JSON with access_token for Web Playback SDK initialization
    """
    session_id = "default_user"  # TODO: Use actual session management

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
async def refresh_token(request: Request):
    """Refresh the access token using refresh token.

    Called automatically when access token expires.
    """
    session_id = "default_user"  # TODO: Use actual session management

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
async def logout(request: Request):
    """Log out user and clear tokens."""
    session_id = "default_user"  # TODO: Use actual session management

    if session_id in _user_tokens:
        del _user_tokens[session_id]
        logger.info("Logged out session: %s", session_id)

    return JSONResponse({"message": "Logged out successfully"})


@router.post("/restore")
async def restore_tokens(request: Request):
    """Restore OAuth tokens from client-side localStorage.

    Called when the Spotify player iframe loads and has tokens saved
    in localStorage that the server may have lost (e.g., after restart).
    Uses the refresh_token to obtain a fresh access_token.
    """
    body = await request.json()
    session_id = "default_user"

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

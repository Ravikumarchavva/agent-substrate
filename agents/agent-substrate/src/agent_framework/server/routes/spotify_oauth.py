"""Spotify OAuth authentication routes for Web Playback SDK."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from agent_framework.configs.settings import settings
from agent_framework.services.spotify_auth import SpotifyAuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/spotify", tags=["spotify-oauth"])

# In-memory token storage (use Redis/database in production)
_user_tokens: Dict[str, Dict[str, Any]] = {}


def get_auth_service() -> SpotifyAuthService:
    """Get Spotify OAuth service instance."""
    redirect_uri = settings.SPOTIFY_REDIRECT_URI or "http://localhost:8001/auth/spotify/callback"
    
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
    
    # Store state in session (simplified - use Redis in production)
    request.app.state.spotify_oauth_state = state  
    
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
        logger.error(f"Spotify OAuth error: {error}")
        return HTMLResponse(
            content=f"""
            <html>
                <body>
                    <h1>Spotify Authentication Failed</h1>
                    <p>Error: {error}</p>
                    <script>
                        window.opener?.postMessage({{
                            type: 'spotify_auth_error',
                            error: '{error}'
                        }}, '*');
                        setTimeout(() => window.close(), 3000);
                    </script>
                </body>
            </html>
            """,
            status_code=400,
        )
    
    auth_service = get_auth_service()
    
    # Validate state to prevent CSRF
    stored_state = getattr(request.app.state, 'spotify_oauth_state', None)
    if not stored_state or state != stored_state:
        logger.error("Invalid OAuth state parameter")
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    
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
        
        logger.info(f"Successfully stored Spotify tokens for session: {session_id}")
        
        # Return HTML that sends tokens to parent window and closes popup
        return HTMLResponse(
            content=f"""
            <html>
                <head>
                    <title>Spotify Authentication Success</title>
                </head>
                <body>
                    <h1>✅ Connected to Spotify!</h1>
                    <p>You can close this window...</p>
                    <script>
                        // Send tokens to parent window (opener)
                        if (window.opener) {{
                            window.opener.postMessage({{
                                type: 'spotify_auth_success',
                                tokens: {{
                                    access_token: '{token_data["access_token"]}',
                                    expires_in: {token_data.get("expires_in", 3600)},
                                    scope: '{token_data.get("scope", "")}'
                                }}
                            }}, '*');
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
        logger.error(f"Failed to exchange OAuth code: {e}")
        raise HTTPException(status_code=500, detail=f"Token exchange failed: {str(e)}")


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
            detail="Not authenticated. Please log in with Spotify first."
        )
    
    return JSONResponse({
        "access_token": tokens["access_token"],
        "expires_in": tokens["expires_in"],
    })


@router.post("/refresh")
async def refresh_token(request: Request):
    """Refresh the access token using refresh token.
    
    Called automatically when access token expires.
    """
    session_id = "default_user"  # TODO: Use actual session management
    
    tokens = _user_tokens.get(session_id)
    if not tokens or not tokens.get("refresh_token"):
        raise HTTPException(
            status_code=401,
            detail="No refresh token available. Please log in again."
        )
    
    auth_service = get_auth_service()
    
    try:
        new_token_data = await auth_service.refresh_access_token(tokens["refresh_token"])
        
        # Update stored tokens
        _user_tokens[session_id].update({
            "access_token": new_token_data["access_token"],
            "expires_in": new_token_data.get("expires_in", 3600),
        })
        
        logger.info(f"Refreshed access token for session: {session_id}")
        
        return JSONResponse({
            "access_token": new_token_data["access_token"],
            "expires_in": new_token_data.get("expires_in", 3600),
        })
        
    except Exception as e:
        logger.error(f"Failed to refresh token: {e}")
        raise HTTPException(status_code=500, detail=f"Token refresh failed: {str(e)}")


@router.post("/logout")
async def logout(request: Request):
    """Log out user and clear tokens."""
    session_id = "default_user"  # TODO: Use actual session management
    
    if session_id in _user_tokens:
        del _user_tokens[session_id]
        logger.info(f"Logged out session: {session_id}")
    
    return JSONResponse({"message": "Logged out successfully"})

"""Spotify OAuth Authorization Code flow for user authentication.

Enables Web Playback SDK integration for full track playback.
Requires Spotify Premium subscription.
"""

from __future__ import annotations

import base64
import logging
import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# Required scopes for Web Playback SDK
SPOTIFY_SCOPES = [
    "streaming",                    # Play tracks via Web Playback SDK
    "user-read-email",              # Read user's email
    "user-read-private",            # Read subscription type (Premium required)
    "user-modify-playback-state",   # Control playback (play/pause/skip)
    "user-read-playback-state",     # Read current playback state
]


class SpotifyAuthService:
    """OAuth Authorization Code flow with PKCE for user authentication."""

    AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
    TOKEN_URL = "https://accounts.spotify.com/api/token"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ):
        """Initialize Spotify OAuth service.

        Args:
            client_id: Spotify app client ID
            client_secret: Spotify app client secret
            redirect_uri: OAuth callback URL (must be registered in Spotify Dashboard)
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.state_store: Dict[str, bool] = {}  # Use Redis in production

    def get_authorization_url(self) -> tuple[str, str]:
        """Generate OAuth authorization URL for user login.

        Returns:
            Tuple of (authorization_url, state) where state should be validated in callback
        """
        state = secrets.token_urlsafe(16)
        self.state_store[state] = True

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "state": state,
            "scope": " ".join(SPOTIFY_SCOPES),
            "show_dialog": "false",  # Don't force auth dialog if already authorized
        }

        auth_url = f"{self.AUTHORIZE_URL}?{urlencode(params)}"
        logger.info(f"Generated Spotify OAuth URL for redirect_uri: {self.redirect_uri}")
        return auth_url, state

    def validate_state(self, state: str) -> bool:
        """Validate OAuth state parameter to prevent CSRF attacks.

        Args:
            state: State value from OAuth callback

        Returns:
            True if valid, False otherwise
        """
        if state in self.state_store:
            del self.state_store[state]  # One-time use
            return True
        return False

    async def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access + refresh tokens.

        Args:
            code: Authorization code from OAuth callback

        Returns:
            Token response with access_token, refresh_token, expires_in, scope

        Raises:
            httpx.HTTPStatusError: If token request fails
        """
        # Create Basic Auth header
        auth_header = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.TOKEN_URL,
                headers={
                    "Authorization": f"Basic {auth_header}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            token_data = resp.json()

        logger.info(
            f"Exchanged auth code for access token (expires in {token_data.get('expires_in')}s)"
        )
        return token_data

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh an expired access token using refresh token.

        Args:
            refresh_token: Refresh token from previous token response

        Returns:
            New token response with fresh access_token

        Raises:
            httpx.HTTPStatusError: If refresh fails
        """
        auth_header = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.TOKEN_URL,
                headers={
                    "Authorization": f"Basic {auth_header}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            token_data = resp.json()

        logger.info(f"Refreshed access token (expires in {token_data.get('expires_in')}s)")
        return token_data

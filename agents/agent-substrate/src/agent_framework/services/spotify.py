"""Spotify Web API integration via Client Credentials flow.

Provides search, track metadata, and artist info.
Playback is handled client-side using Spotify 30-second preview URLs
(no Premium required) and links to open tracks in the Spotify app.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"


class SpotifyService:
    """Lightweight async Spotify API client supporting both OAuth and Client Credentials."""

    def __init__(
        self, 
        client_id: str, 
        client_secret: str,
        oauth_token: Optional[str] = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._oauth_token = oauth_token  # User OAuth token (if authenticated)
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    async def _ensure_token(self) -> str:
        """Get access token (OAuth if available, otherwise Client Credentials)."""
        # Prefer OAuth token if available
        if self._oauth_token:
            return self._oauth_token
            
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        credentials = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    SPOTIFY_TOKEN_URL,
                    headers={
                        "Authorization": f"Basic {credentials}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data={"grant_type": "client_credentials"},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Spotify authentication failed: %s", e)
            raise ValueError(
                "Invalid Spotify credentials. Please check SPOTIFY_CLIENT_ID and "
                "SPOTIFY_CLIENT_SECRET in your .env file. Get valid credentials from "
                "https://developer.spotify.com/dashboard"
            ) from e

        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 3600)
        logger.info("Spotify access token refreshed (expires in %ds)", data.get("expires_in"))
        return self._access_token

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make an authenticated GET request to the Spotify API."""
        token = await self._ensure_token()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{SPOTIFY_API_BASE}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                    timeout=15.0,
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            # Log the response body for debugging
            try:
                error_body = e.response.text
            except Exception:
                error_body = "(could not read response body)"
            logger.error(
                "Spotify API error %s for %s: %s",
                e.response.status_code, path, error_body,
            )
            if e.response.status_code == 403:
                raise ValueError(
                    "Spotify API access denied. Your credentials may be invalid or expired. "
                    "Please update SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in your .env file. "
                    "Get credentials from https://developer.spotify.com/dashboard"
                ) from e
            raise

    async def search_tracks(
        self,
        query: str,
        limit: int = 20,
        market: Optional[str] = None,
        prefer_previews: bool = True,
    ) -> List[Dict[str, Any]]:
        """Search Spotify for tracks.

        Returns a simplified list of track objects suitable for the UI.
        
        Args:
            query: Search query string
            limit: Maximum number of tracks to return
            market: ISO 3166-1 alpha-2 country code (e.g., "US", "IN", "GB"). 
                    If None, Spotify auto-detects based on user's IP/location.
            prefer_previews: If True, prioritize tracks with preview URLs
        """
        # Ensure limit is valid (Spotify requires 1-50)
        safe_limit = max(1, min(limit, 50))
        
        params: Dict[str, Any] = {
            "q": query,
            "type": "track",
            "limit": safe_limit,
        }
        
        # Add market if explicitly specified
        if market:
            params["market"] = market
        
        data = await self._get("/search", params=params)

        tracks: List[Dict[str, Any]] = []
        for item in data.get("tracks", {}).get("items", []):
            artists = [a["name"] for a in item.get("artists", [])]
            album = item.get("album", {})
            album_images = album.get("images", [])
            # Pick medium-sized image (300px) or first available
            cover_url = ""
            for img in album_images:
                cover_url = img.get("url", "")
                if img.get("height", 0) <= 300:
                    break

            tracks.append({
                "id": item["id"],
                "name": item["name"],
                "artists": artists,
                "artist": ", ".join(artists),
                "album": album.get("name", ""),
                "album_id": album.get("id", ""),
                "cover_url": cover_url,
                "duration_ms": item.get("duration_ms", 0),
                "preview_url": item.get("preview_url"),
                "spotify_url": item.get("external_urls", {}).get("spotify", ""),
                "uri": item.get("uri", ""),
                "popularity": item.get("popularity", 0),
                "track_number": item.get("track_number", 0),
            })

        # Prioritize tracks with preview URLs
        if prefer_previews:
            with_preview = [t for t in tracks if t["preview_url"]]
            without_preview = [t for t in tracks if not t["preview_url"]]
            tracks = with_preview + without_preview
            
            # Log preview availability for debugging
            if with_preview:
                logger.info(f"Found {len(with_preview)}/{len(tracks)} tracks with previews for query: {query}")
            else:
                logger.warning(f"No preview URLs available for query: {query} (market: {market})")

        return tracks[:limit]

    async def get_recommendations(
        self,
        seed_tracks: Optional[List[str]] = None,
        seed_artists: Optional[List[str]] = None,
        seed_genres: Optional[List[str]] = None,
        limit: int = 20,
        market: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get track recommendations based on seeds."""
        params: Dict[str, Any] = {
            "limit": min(limit, 100),
        }
        
        # Only add market if explicitly specified
        if market:
            params["market"] = market
            
        if seed_tracks:
            params["seed_tracks"] = ",".join(seed_tracks[:5])
        if seed_artists:
            params["seed_artists"] = ",".join(seed_artists[:5])
        if seed_genres:
            params["seed_genres"] = ",".join(seed_genres[:5])

        # Must have at least one seed
        if not any(k.startswith("seed_") for k in params):
            return []

        data = await self._get("/recommendations", params=params)

        tracks: List[Dict[str, Any]] = []
        for item in data.get("tracks", []):
            artists = [a["name"] for a in item.get("artists", [])]
            album = item.get("album", {})
            album_images = album.get("images", [])
            cover_url = ""
            for img in album_images:
                cover_url = img.get("url", "")
                if img.get("height", 0) <= 300:
                    break

            tracks.append({
                "id": item["id"],
                "name": item["name"],
                "artists": artists,
                "artist": ", ".join(artists),
                "album": album.get("name", ""),
                "cover_url": cover_url,
                "duration_ms": item.get("duration_ms", 0),
                "preview_url": item.get("preview_url"),
                "spotify_url": item.get("external_urls", {}).get("spotify", ""),
                "uri": item.get("uri", ""),
                "popularity": item.get("popularity", 0),
            })

        return tracks

    async def get_artist(self, artist_id: str) -> Dict[str, Any]:
        """Get artist details."""
        return await self._get(f"/artists/{artist_id}")

    async def get_available_genres(self) -> List[str]:
        """Get available genre seeds for recommendations."""
        data = await self._get("/recommendations/available-genre-seeds")
        return data.get("genres", [])

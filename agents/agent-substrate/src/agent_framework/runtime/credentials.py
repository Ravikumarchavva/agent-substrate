"""
Credential Service for Agent Framework
Handles secure storage and retrieval of OAuth tokens with AES-256 encryption.
"""
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import asyncpg
from cryptography.fernet import Fernet
import httpx


class CredentialService:
    """Manages encrypted credential storage in PostgreSQL"""

    def __init__(self, db_pool: asyncpg.Pool):
        self.db = db_pool
        
        # Load encryption key from environment
        # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
        encryption_key = os.environ.get("ENCRYPTION_KEY")
        if not encryption_key:
            raise ValueError("ENCRYPTION_KEY environment variable not set")
        
        self.cipher = Fernet(encryption_key.encode())

    def encrypt_token(self, token: str) -> str:
        """Encrypt a token using AES-256"""
        return self.cipher.encrypt(token.encode()).decode()

    def decrypt_token(self, encrypted_token: str) -> str:
        """Decrypt a token"""
        return self.cipher.decrypt(encrypted_token.encode()).decode()

    async def store_credential(
        self,
        user_id: str,
        provider: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        expires_in: int = 3600,
        scope: Optional[str] = None,
        token_type: str = "Bearer"
    ) -> None:
        """
        Store encrypted OAuth credentials for a user.
        
        Args:
            user_id: UUID of the user
            provider: OAuth provider name ('spotify', 'google', etc.)
            access_token: OAuth access token
            refresh_token: Optional refresh token
            expires_in: Token expiry in seconds (default 3600)
            scope: OAuth scopes granted
            token_type: Token type (default 'Bearer')
        """
        encrypted_access = self.encrypt_token(access_token)
        encrypted_refresh = self.encrypt_token(refresh_token) if refresh_token else None
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        await self.db.execute(
            """
            INSERT INTO user_credentials 
                (user_id, provider, access_token, refresh_token, token_type, expires_at, scope)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id, provider) 
            DO UPDATE SET 
                access_token = $3,
                refresh_token = $4,
                token_type = $5,
                expires_at = $6,
                scope = $7,
                updated_at = NOW()
            """,
            user_id,
            provider,
            encrypted_access,
            encrypted_refresh,
            token_type,
            expires_at,
            scope,
        )

    async def get_credential(self, user_id: str, provider: str) -> Optional[Dict[str, Any]]:
        """
        Get decrypted OAuth credentials for a user.
        Automatically refreshes expired tokens if refresh_token is available.
        
        Args:
            user_id: UUID of the user
            provider: OAuth provider name
            
        Returns:
            Dict with 'access_token', 'refresh_token', 'expires_at', 'scope'
            None if credentials not found
        """
        row = await self.db.fetchrow(
            """
            SELECT access_token, refresh_token, expires_at, scope, token_type
            FROM user_credentials
            WHERE user_id = $1 AND provider = $2
            """,
            user_id,
            provider,
        )

        if not row:
            return None

        # Check if token is expired
        if row["expires_at"] and row["expires_at"] < datetime.utcnow():
            # Try to refresh
            if row["refresh_token"]:
                refreshed = await self.refresh_token(user_id, provider)
                if refreshed:
                    return refreshed
            return None

        return {
            "access_token": self.decrypt_token(row["access_token"]),
            "refresh_token": self.decrypt_token(row["refresh_token"]) if row["refresh_token"] else None,
            "expires_at": row["expires_at"],
            "scope": row["scope"],
            "token_type": row["token_type"],
        }

    async def refresh_token(self, user_id: str, provider: str) -> Optional[Dict[str, Any]]:
        """
        Refresh an expired OAuth token.
        Currently supports Spotify and Google.
        
        Args:
            user_id: UUID of the user
            provider: OAuth provider name
            
        Returns:
            Dict with new credentials, or None if refresh failed
        """
        row = await self.db.fetchrow(
            """
            SELECT refresh_token
            FROM user_credentials
            WHERE user_id = $1 AND provider = $2
            """,
            user_id,
            provider,
        )

        if not row or not row["refresh_token"]:
            return None

        refresh_token = self.decrypt_token(row["refresh_token"])

        # Provider-specific refresh logic
        if provider == "spotify":
            return await self._refresh_spotify(user_id, refresh_token)
        elif provider == "google":
            return await self._refresh_google(user_id, refresh_token)
        else:
            return None

    async def _refresh_spotify(self, user_id: str, refresh_token: str) -> Optional[Dict[str, Any]]:
        """Refresh Spotify access token"""
        import base64

        client_id = os.environ.get("SPOTIFY_CLIENT_ID")
        client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")

        if not client_id or not client_secret:
            return None

        basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://accounts.spotify.com/api/token",
                    headers={"Authorization": f"Basic {basic_auth}"},
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                )

                if response.status_code != 200:
                    return None

                data = response.json()
                
                # Store new credentials
                await self.store_credential(
                    user_id=user_id,
                    provider="spotify",
                    access_token=data["access_token"],
                    refresh_token=data.get("refresh_token", refresh_token),
                    expires_in=data.get("expires_in", 3600),
                    scope=data.get("scope"),
                )

                return {
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token", refresh_token),
                    "expires_in": data.get("expires_in", 3600),
                }

            except Exception as e:
                print(f"[CredentialService] Spotify refresh failed: {e}")
                return None

    async def _refresh_google(self, user_id: str, refresh_token: str) -> Optional[Dict[str, Any]]:
        """Refresh Google access token"""
        client_id = os.environ.get("GOOGLE_CLIENT_ID")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

        if not client_id or not client_secret:
            return None

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                )

                if response.status_code != 200:
                    return None

                data = response.json()

                # Store new credentials
                await self.store_credential(
                    user_id=user_id,
                    provider="google",
                    access_token=data["access_token"],
                    refresh_token=refresh_token,  # Google doesn't return new refresh token
                    expires_in=data.get("expires_in", 3600),
                    scope=data.get("scope"),
                )

                return {
                    "access_token": data["access_token"],
                    "refresh_token": refresh_token,
                    "expires_in": data.get("expires_in", 3600),
                }

            except Exception as e:
                print(f"[CredentialService] Google refresh failed: {e}")
                return None

    async def delete_credential(self, user_id: str, provider: str) -> bool:
        """
        Delete stored credentials for a provider.
        
        Args:
            user_id: UUID of the user
            provider: OAuth provider name
            
        Returns:
            True if deleted, False if not found
        """
        result = await self.db.execute(
            """
            DELETE FROM user_credentials
            WHERE user_id = $1 AND provider = $2
            """,
            user_id,
            provider,
        )
        return result != "DELETE 0"

    async def list_user_providers(self, user_id: str) -> list[str]:
        """
        List all providers a user has connected.
        
        Args:
            user_id: UUID of the user
            
        Returns:
            List of provider names
        """
        rows = await self.db.fetch(
            """
            SELECT provider 
            FROM user_credentials
            WHERE user_id = $1
            """,
            user_id,
        )
        return [row["provider"] for row in rows]


# Singleton instance
_credential_service: Optional[CredentialService] = None


def init_credential_service(db_pool: asyncpg.Pool) -> CredentialService:
    """Initialize the credential service singleton"""
    global _credential_service
    _credential_service = CredentialService(db_pool)
    return _credential_service


def get_credential_service() -> CredentialService:
    """Get the credential service singleton"""
    if _credential_service is None:
        raise RuntimeError("CredentialService not initialized. Call init_credential_service first.")
    return _credential_service

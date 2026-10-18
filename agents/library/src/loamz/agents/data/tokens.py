"""`DataTokenMinter`: signs short-lived Ed25519 tokens for agents and runs.

The data platform fetches the public key at the well-known JWKS URL over HTTPS;
no private key is ever shared between machines.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEFAULT_TOKEN_SECONDS = 300
MAX_TOKEN_SECONDS = 330
MIN_TOKEN_SECONDS = 60

DATA_SCOPES = frozenset(
    {
        "data:read",
        "files:read",
        "files:write",
        "documents:read",
        "documents:write",
        "memory:read",
        "memory:write",
    }
)


def load_or_create_key(path: Path) -> Ed25519PrivateKey:
    """The token-signing key; created once, private to its owner."""
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"{path} must hold an Ed25519 private key")
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    return key


def _kid(key: Ed25519PrivateKey) -> str:
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return hashlib.sha256(public).hexdigest()[:16]


class DataTokenMinter:
    """Mints short-lived delegated tokens for the Loamz Data platform."""

    def __init__(
        self,
        key_path: Path,
        issuer: str = "loamz-agents",
        allowed_scopes: frozenset[str] = DATA_SCOPES,
    ) -> None:
        self.key = load_or_create_key(key_path)
        self.kid = _kid(self.key)
        self.issuer = issuer
        self.allowed_scopes = allowed_scopes

    def mint(
        self,
        tenant_id: str,
        user_id: str | None = None,
        *,
        agent_id: str | None = None,
        run_id: str | None = None,
        thread_id: str | None = None,
        scopes: set[str] | frozenset[str] | None = None,
        ttl_seconds: int = DEFAULT_TOKEN_SECONDS,
    ) -> str:
        """Mint a short-lived token."""
        now = int(time.time())
        ttl = max(MIN_TOKEN_SECONDS, min(ttl_seconds, MAX_TOKEN_SECONDS))
        granted = sorted(self.allowed_scopes if scopes is None else (scopes & self.allowed_scopes))
        payload: dict[str, object] = {
            "iss": self.issuer,
            "sub": user_id or f"tenant:{tenant_id}",
            "tenant_id": tenant_id,
            "scope": " ".join(granted),
            "iat": now,
            "exp": now + ttl,
        }
        if user_id:
            payload["user_id"] = user_id
        if agent_id:
            payload["act"] = {"sub": f"agent:{agent_id}"}
        if run_id:
            payload["run_id"] = run_id
        if thread_id:
            payload["thread_id"] = thread_id

        return jwt.encode(
            payload,
            self.key,
            algorithm="EdDSA",
            headers={"kid": self.kid},
        )

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        """Public key exposed for token verification."""
        raw_pub = self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        x_b64 = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode("ascii")
        return {
            "keys": [
                {
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "kid": self.kid,
                    "x": x_b64,
                    "use": "sig",
                }
            ]
        }


"""Signed, short-lived statement tickets: the only thing a worker will execute.

The control plane signs a ticket after authorizing and planning a statement; workers hold only
the public key. A ticket names the exact files a statement may read, its rewritten SQL and its
limits, so a worker never needs catalog access or user credentials.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives import serialization

from substrate_data.core.errors import Problem

TICKET_VERSION = 1
ISSUER = "substrate-data"
AUDIENCE = "substrate-data-worker"
TYPE = "substrate-ticket+jwt"


class TicketSigner:
    def __init__(self, private_key_file: Path, ttl_seconds: int):
        self.key = serialization.load_pem_private_key(private_key_file.read_bytes(), None)
        self.ttl = ttl_seconds

    def sign(
        self, claims: dict[str, Any], ticket_id: str | None = None, ttl: int | None = None
    ) -> tuple[str, str]:
        now = int(time.time())
        ticket_id = ticket_id or uuid4().hex
        payload = {
            **claims,
            "v": TICKET_VERSION,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "jti": ticket_id,
            "iat": now,
            "exp": now + (ttl or self.ttl),
        }
        return jwt.encode(payload, self.key, algorithm="EdDSA", headers={"typ": TYPE}), ticket_id


class TicketVerifier:
    def __init__(self, public_key_file: Path):
        self.key = serialization.load_pem_public_key(public_key_file.read_bytes())
        self.lock = threading.Lock()
        self.seen: dict[str, int] = {}

    def verify(self, token: str, *, single_use: bool = True) -> dict[str, Any]:
        try:
            if jwt.get_unverified_header(token).get("typ") != TYPE:
                raise jwt.InvalidTokenError("wrong type")
            claims = jwt.decode(
                token,
                self.key,
                algorithms=["EdDSA"],
                audience=AUDIENCE,
                issuer=ISSUER,
                leeway=5,
                options={"require": ["exp", "iat", "jti", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise Problem(401, "invalid-ticket", f"Ticket rejected: {type(exc).__name__}") from None
        if claims.get("v") != TICKET_VERSION:
            raise Problem(401, "invalid-ticket", "Unsupported ticket version")
        if single_use:
            now = int(time.time())
            with self.lock:
                self.seen = {k: v for k, v in self.seen.items() if v > now}
                if claims["jti"] in self.seen:
                    raise Problem(401, "invalid-ticket", "Ticket already used")
                self.seen[claims["jti"]] = claims["exp"] + 10
        return claims

"""A fake Keycloak realm for tests: an RSA key, a JWKS served from memory, and a token minter.

realm = FakeRealm()
app.state.token_verifier = realm.verifier()
headers = {"Authorization": f"Bearer {realm.token('alice', org='acme', roles=['tenant_admin'])}"}
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from substrate_identity import JwksSource, PrimaryIssuer, TokenVerifier

ISSUER = "http://keycloak.test/realms/substrate"
AUDIENCE = "agent-substrate"
JWKS_URL = "http://keycloak.test/realms/substrate/protocol/openid-connect/certs"


class FakeRealm:
    def __init__(self, issuer: str = ISSUER, audience: str = AUDIENCE):
        self.issuer, self.audience = issuer, audience
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-key"

    def jwks(self) -> dict[str, Any]:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def verifier(self) -> TokenVerifier:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=self.jwks())
        )
        keys = JwksSource(
            JWKS_URL, client=httpx.Client(transport=transport), insecure_http=True
        )
        return TokenVerifier(
            PrimaryIssuer(self.issuer, self.audience, jwks_url=JWKS_URL),
            primary_keys=keys,
            insecure_http=True,
        )

    def token(
        self,
        sub: str = "user-1",
        *,
        org: str | None = "org-1",
        roles: Iterable[str] = (),
        client_roles: Iterable[str] = (),
        aud: str | list[str] | None = None,
        typ: str = "Bearer",
        scope: str = "openid",
        email: str = "",
        username: str | None = None,
        lifetime: int = 300,
        key: Any = None,
        **claims: Any,
    ) -> str:
        now = int(time.time())
        body: dict[str, Any] = {
            "iss": self.issuer,
            "aud": aud if aud is not None else self.audience,
            "sub": sub,
            "iat": now,
            "exp": now + lifetime,
            "jti": uuid.uuid4().hex,
            "typ": typ,
            "scope": scope,
            "preferred_username": username or sub,
            "realm_access": {"roles": list(roles)},
            **claims,
        }
        if email:
            body["email"] = email
        if org is not None:
            body["organization"] = {"acme": {"id": org}}
        if client_roles:
            body["resource_access"] = {self.audience: {"roles": list(client_roles)}}
        return jwt.encode(
            body, key or self.key, algorithm="RS256", headers={"kid": self.kid}
        )

    def bearer(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(*args, **kwargs)}"}

    def hs256(self, secret: str = "s" * 40, **extra: Any) -> str:
        """The retired shared-secret format; every service must refuse it."""
        now = int(time.time())
        body = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": "user-1",
            "iat": now,
            "exp": now + 300,
            "typ": "Bearer",
            "tenant_id": "org-1",
            "type": "access",
            "organization": {"acme": {"id": "org-1"}},
            **extra,
        }
        return jwt.encode(body, secret, algorithm="HS256", headers={"kid": self.kid})

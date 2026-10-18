"""Validate access tokens: the primary OIDC provider's, plus explicitly trusted issuers'."""

from __future__ import annotations

import ssl
import threading
import time
from typing import Any

import httpx
import jwt

from substrate_data.auth.principal import Principal
from substrate_data.core.errors import Problem
from substrate_data.core.models import Settings, TrustedIssuer

ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "EdDSA"]
SERVICE_PREFIX = "service-account-"
CONTEXT_CLAIMS = ("tenant_id", "thread_id", "run_id", "agent_id")
TOKEN_ID_CLAIM = "jti"  # recorded as `token_id`, so a statement names the exact token that ran it


def unauthorized(detail: str) -> Problem:
    return Problem(401, "unauthorized", detail)


class RemoteKeys:
    """An issuer's public keys, fetched over HTTPS from its JWKS endpoint.

    Keys are cached; a token naming a key not yet seen triggers one refresh (rotation needs no
    coordination), but never more than one per `min_refresh` seconds, so a caller spraying random
    key ids cannot turn this service into a request amplifier. If the issuer is unreachable, keys
    already fetched keep working for `stale_seconds` (an outage on the agent side must not become
    an outage here, and must not extend trust forever).
    """

    MAX_BYTES = 256 * 1024

    def __init__(
        self,
        trusted: TrustedIssuer,
        *,
        ttl: float = 300,
        min_refresh: float = 30,
        stale_seconds: float = 3600,
        client: httpx.Client | None = None,
    ):
        self.trusted = trusted
        self.ttl, self.min_refresh, self.stale = ttl, min_refresh, stale_seconds
        self.client = client or httpx.Client(
            verify=ssl.create_default_context(cafile=str(trusted.jwks_ca_file))
            if trusted.jwks_ca_file
            else True,
            timeout=httpx.Timeout(5),
            follow_redirects=False,
        )
        self.lock = threading.Lock()
        self.keys: dict[str, Any] = {}
        self.fetched = float("-inf")  # last successful fetch
        self.attempted = float("-inf")  # last attempt of any outcome

    def refresh(self) -> None:
        """Re-read the endpoint. Caller holds the lock."""
        self.attempted = time.monotonic()
        response = self.client.get(self.trusted.jwks_uri, headers={"Accept": "application/json"})
        response.raise_for_status()
        if len(response.content) > self.MAX_BYTES:
            raise ValueError("JWKS document is too large")
        keys: dict[str, Any] = {}
        for entry in response.json().get("keys", []):
            if entry.get("alg") not in (None, *self.trusted.algorithms):
                continue  # never trust a key for an algorithm the operator did not allow
            jwk = jwt.PyJWK(entry)
            keys[jwk.key_id or ""] = jwk.key
        self.keys, self.fetched = keys, time.monotonic()

    def key_for(self, token: str) -> Any:
        kid = jwt.get_unverified_header(token).get("kid") or ""
        with self.lock:
            now = time.monotonic()
            if (
                kid not in self.keys or now - self.fetched > self.ttl
            ) and now - self.attempted >= self.min_refresh:
                try:
                    self.refresh()
                except httpx.HTTPError, ValueError, jwt.PyJWTError:
                    pass  # judged below: cached keys may still be inside the stale window
            if not self.keys or time.monotonic() - self.fetched > self.stale:
                raise Problem(
                    503,
                    "identity-provider-unavailable",
                    "Cannot reach the token issuer's key endpoint",
                )
            key = self.keys.get(kid)
        if key is None:
            raise jwt.InvalidKeyError("unknown signing key")
        return key


class TokenVerifier:
    def __init__(self, settings: Settings, jwks: jwt.PyJWKClient | None = None):
        self.settings = settings
        # Keys are cached and refreshed when an unknown `kid` appears (IdP key rotation).
        self.jwks = jwks or jwt.PyJWKClient(settings.jwks_url, cache_keys=True, lifespan=300)
        self.trusted = {t.issuer: (t, RemoteKeys(t)) for t in settings.trusted_issuers}

    def verify(self, authorization: str | None) -> Principal:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise unauthorized("A bearer access token is required")
        try:
            issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
        except jwt.PyJWTError:
            raise unauthorized("Access token rejected: malformed") from None
        if issuer in self.trusted:
            return self.verify_trusted(token, *self.trusted[issuer])
        if issuer != self.settings.oidc_issuer:
            raise unauthorized("Access token rejected: untrusted issuer")
        return self.verify_primary(token)

    def verify_primary(self, token: str) -> Principal:
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=self.settings.oidc_audience,
                issuer=self.settings.oidc_issuer,
                leeway=30,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWKClientConnectionError:
            raise Problem(
                503, "identity-provider-unavailable", "Cannot reach the identity provider"
            ) from None
        except jwt.PyJWTError as exc:
            raise unauthorized(f"Access token rejected: {type(exc).__name__}") from None
        # Keycloak marks access tokens `typ: Bearer`; refuse ID and refresh tokens.
        if claims.get("typ", "Bearer") != "Bearer":
            raise unauthorized("Access token rejected: not an access token")
        username = claims.get("preferred_username") or claims["sub"]
        groups = frozenset(str(g).lstrip("/") for g in claims.get("groups", []))
        return Principal(
            subject=claims["sub"],
            name=username.removeprefix(SERVICE_PREFIX),
            kind="service" if username.startswith(SERVICE_PREFIX) else "user",
            groups=groups,
            email=claims.get("email"),
            actor=_actor(claims),
            is_admin=self.settings.admin_group in groups,
            read_only=_scopes(claims) == {"data:read"},
            scopes=_restricted(claims),
            context=_context(claims),
        )

    def verify_trusted(self, token: str, trusted: TrustedIssuer, keys: RemoteKeys) -> Principal:
        try:
            claims = jwt.decode(
                token,
                keys.key_for(token),
                algorithms=trusted.algorithms,
                audience=trusted.audience,
                issuer=trusted.issuer,
                leeway=10,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise unauthorized(f"Access token rejected: {type(exc).__name__}") from None
        name = str(claims.get(trusted.username_claim) or claims["sub"])
        groups = claims.get(trusted.group_claim, [])
        return Principal(
            subject=str(claims["sub"]),
            name=name,
            kind="user",
            groups=frozenset(str(g) for g in groups) if isinstance(groups, list) else frozenset(),
            email=claims.get("email"),
            actor=_actor(claims),
            is_admin=False,  # a trusted issuer can never mint an administrator
            read_only=trusted.read_only or _scopes(claims) == {"data:read"},
            scopes=_restricted(claims) or frozenset({"data:read"}),
            context=_context(claims),
        )


def _actor(claims: dict[str, Any]) -> str | None:
    act = claims.get("act")
    return str(act["sub"]) if isinstance(act, dict) and act.get("sub") else None


RESTRICTED_PREFIXES = ("data:", "files:", "documents:", "memory:")


def _restricted(claims: dict[str, Any]) -> frozenset[str] | None:
    """The delegated scopes of a token, or None for an ordinary user token (grants alone limit it)."""
    held = frozenset(s for s in _scopes(claims) if s.startswith(RESTRICTED_PREFIXES))
    return held or None


def _scopes(claims: dict[str, Any]) -> set[str]:
    raw = claims.get("scope") or ""
    return set(raw.split()) if isinstance(raw, str) else set()


def _context(claims: dict[str, Any]) -> dict[str, str]:
    context = {k: str(claims[k]) for k in CONTEXT_CLAIMS if claims.get(k)}
    if claims.get(TOKEN_ID_CLAIM):
        context["token_id"] = str(claims[TOKEN_ID_CLAIM])
    return context

"""Verify access tokens from the primary OIDC issuer (Keycloak) and from explicitly trusted issuers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import jwt

from loamz.identity.errors import (
    AmbiguousOrganization,
    IdentityProviderUnavailable,
    InvalidToken,
    MissingToken,
    OrganizationError,
    UntrustedIssuer,
)
from loamz.identity.identity import Identity
from loamz.identity.jwks import (
    ALLOWED_ALGORITHMS,
    JwksSource,
    discover_jwks_uri,
    make_client,
)

SERVICE_PREFIX = "service-account-"
SCOPE_PREFIXES = ("data:", "files:", "documents:", "memory:")
CONTEXT_CLAIMS = ("thread_id", "run_id", "agent_id")
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]


def _audiences(value: str | Sequence[str]) -> tuple[str, ...]:
    result = (value,) if isinstance(value, str) else tuple(value)
    if not result or not all(result):
        raise ValueError("at least one audience is required")
    return result


@dataclass(frozen=True)
class PrimaryIssuer:
    """The realm users and service accounts sign in to (Keycloak).

    Give `jwks_url`, or leave it empty to discover it from `{issuer}/.well-known/openid-configuration`.
    """

    issuer: str
    audience: str | Sequence[str]
    jwks_url: str | None = None
    algorithms: Sequence[str] = ALLOWED_ALGORITHMS
    ca_file: Path | None = None
    leeway: float = 30


@dataclass(frozen=True)
class TrustedIssuer:
    """Another system whose signed tokens this service accepts (for example the Loamz agent server's per-run token).

    Its principals are never administrators and carry no roles. `allowed_scopes` caps what its tokens may
    do: scopes outside it are dropped. A token without any delegated scope gets `default_scopes`.
    """

    issuer: str
    audience: str | Sequence[str]
    jwks_uri: str
    algorithms: Sequence[str] = ("EdDSA",)
    jwks_ca_file: Path | None = None
    allowed_scopes: frozenset[str] | None = None
    default_scopes: frozenset[str] = frozenset({"data:read"})
    username_claim: str = "sub"
    group_claim: str = "groups"
    leeway: float = 10


@dataclass
class _Trusted:
    config: TrustedIssuer
    keys: JwksSource
    audiences: tuple[str, ...] = field(default=())


class TokenVerifier:
    """Thread-safe. `verify` blocks only on a JWKS refresh; `averify` runs it in a worker thread."""

    def __init__(
        self,
        primary: PrimaryIssuer | None = None,
        trusted: Sequence[TrustedIssuer] = (),
        *,
        require_organization: bool = True,
        services_without_organization: bool = True,
        organization_claim: str = "organization",
        scope_prefixes: Sequence[str] = SCOPE_PREFIXES,
        insecure_http: bool = False,
        ttl: float = 300,
        min_refresh: float = 30,
        stale_seconds: float = 3600,
        clock: Callable[[], float] = time.monotonic,
        primary_keys: JwksSource | None = None,
        client: httpx.Client | None = None,
    ):
        """`insecure_http` allows plain-http issuer and JWKS URLs: tests and loopback development only."""
        if primary is None and not trusted:
            raise ValueError("configure a primary issuer, a trusted issuer, or both")
        self.primary = primary
        self.require_organization = require_organization
        self.services_without_organization = services_without_organization
        self.organization_claim = organization_claim
        self.scope_prefixes = tuple(scope_prefixes)
        self.primary_audiences = _audiences(primary.audience) if primary else ()
        options = dict(
            ttl=ttl,
            min_refresh=min_refresh,
            stale_seconds=stale_seconds,
            clock=clock,
            insecure_http=insecure_http,
        )
        self.primary_keys: JwksSource | None = primary_keys
        if primary and primary_keys is None:
            http = client or make_client(primary.ca_file)
            url: str | Callable[[], str] = primary.jwks_url or (
                lambda: discover_jwks_uri(primary.issuer, http, insecure_http=insecure_http)
            )
            self.primary_keys = JwksSource(url, tuple(primary.algorithms), client=http, **options)  # type: ignore[arg-type]
        self.trusted: dict[str, _Trusted] = {}
        for config in trusted:
            self.trusted[config.issuer] = _Trusted(
                config,
                JwksSource(
                    config.jwks_uri,
                    tuple(config.algorithms),
                    ca_file=config.jwks_ca_file,
                    **options,  # type: ignore[arg-type]
                ),
                _audiences(config.audience),
            )

    # ------------------------------------------------------------------ entry points
    def verify(self, authorization: str | None) -> Identity:
        """Verify an `Authorization` header value (`Bearer <token>`)."""
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise MissingToken("A bearer access token is required")
        return self.verify_token(token.strip())

    def verify_token(self, token: str) -> Identity:
        try:
            issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
        except jwt.PyJWTError:
            raise InvalidToken("Access token rejected: malformed") from None
        if issuer in self.trusted:
            return self._verify_trusted(token, self.trusted[issuer])
        if self.primary is None or issuer != self.primary.issuer:
            raise UntrustedIssuer("Access token rejected: untrusted issuer")
        return self._verify_primary(token)

    async def averify(self, authorization: str | None) -> Identity:
        return await asyncio.to_thread(self.verify, authorization)

    async def averify_token(self, token: str) -> Identity:
        return await asyncio.to_thread(self.verify_token, token)

    # ------------------------------------------------------------------ primary issuer
    def _decode(
        self,
        token: str,
        keys: JwksSource,
        *,
        algorithms: Sequence[str],
        audience: tuple[str, ...],
        issuer: str,
        leeway: float,
    ) -> dict[str, Any]:
        try:
            if jwt.get_unverified_header(token).get("alg") not in algorithms:
                raise InvalidToken("Access token rejected: algorithm not allowed")
            return jwt.decode(
                token,
                keys.key_for(token),
                algorithms=list(algorithms),
                audience=list(audience),
                issuer=issuer,
                leeway=leeway,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise InvalidToken(f"Access token rejected: {type(exc).__name__}") from None

    def _verify_primary(self, token: str) -> Identity:
        primary, keys = self.primary, self.primary_keys
        assert primary is not None and keys is not None
        claims = self._decode(
            token,
            keys,
            algorithms=tuple(primary.algorithms),
            audience=self.primary_audiences,
            issuer=primary.issuer,
            leeway=primary.leeway,
        )
        # Keycloak marks access tokens `typ: Bearer`; refuse ID and refresh tokens.
        if claims.get("typ") != "Bearer":
            raise InvalidToken("Access token rejected: not an access token")
        tenant, alias = organization_of(claims.get(self.organization_claim))
        username = str(claims.get("preferred_username") or claims["sub"])
        service = username.startswith(SERVICE_PREFIX)
        # A platform service account (cell-admin) acts across tenants and names the tenant in the request.
        if (
            tenant is None
            and self.require_organization
            and not (service and self.services_without_organization)
        ):
            raise OrganizationError("The token names no organization; select one and sign in again")
        scopes = _token_scopes(claims)
        context = _context(claims)
        if tenant:
            context["tenant_id"] = tenant
        return Identity(
            subject=str(claims["sub"]),
            name=username.removeprefix(SERVICE_PREFIX),
            kind="service" if service else "user",
            email=claims.get("email"),
            tenant_id=tenant,
            organization=alias,
            roles=self._roles(claims),
            groups=frozenset(str(g).lstrip("/") for g in _list(claims.get("groups"))),
            scopes=self._delegated(scopes),
            token_scopes=scopes,
            actor=_actor(claims),
            token_id=_text(claims.get("jti")),
            issuer=primary.issuer,
            context=context,
        )

    def _roles(self, claims: Mapping[str, Any]) -> frozenset[str]:
        roles: set[str] = set()
        realm = claims.get("realm_access")
        if isinstance(realm, dict):
            roles.update(str(r) for r in _list(realm.get("roles")))
        resources = claims.get("resource_access")
        if isinstance(resources, dict):
            for client in self.primary_audiences:
                entry = resources.get(client)
                if isinstance(entry, dict):
                    roles.update(str(r) for r in _list(entry.get("roles")))
        return frozenset(roles)

    def _delegated(self, scopes: frozenset[str]) -> frozenset[str] | None:
        """The delegated scopes of a token, or None for an ordinary user token (roles and grants alone limit it)."""
        held = frozenset(s for s in scopes if s.startswith(self.scope_prefixes))
        return held or None

    # ------------------------------------------------------------------ trusted issuers
    def _verify_trusted(self, token: str, trusted: _Trusted) -> Identity:
        config = trusted.config
        claims = self._decode(
            token,
            trusted.keys,
            algorithms=tuple(config.algorithms),
            audience=trusted.audiences,
            issuer=config.issuer,
            leeway=config.leeway,
        )
        token_scopes = _token_scopes(claims)
        asked = self._delegated(token_scopes) or config.default_scopes
        scopes = asked if config.allowed_scopes is None else asked & config.allowed_scopes
        groups = claims.get(config.group_claim, [])
        context = _context(claims)
        if claims.get("tenant_id"):
            context["tenant_id"] = str(claims["tenant_id"])
        return Identity(
            subject=str(claims["sub"]),
            name=str(claims.get(config.username_claim) or claims["sub"]),
            kind="user",
            email=claims.get("email"),
            tenant_id=context.get("tenant_id"),
            groups=frozenset(str(g) for g in groups) if isinstance(groups, list) else frozenset(),
            scopes=scopes,  # a trusted token is always restricted, never an ordinary user token
            token_scopes=token_scopes,
            actor=_actor(claims),
            token_id=_text(claims.get("jti")),
            issuer=config.issuer,
            trusted_issuer=True,
            context=context,
        )


def organization_of(claim: Any) -> tuple[str | None, str | None]:
    """(tenant_id, alias) from a Keycloak 26 `organization` claim.

    Either a list of aliases (`["acme"]`) or a mapping alias -> {"id": ...} (`{"acme": {"id": "..."}}`);
    a bare string is accepted too. The tenant is the organization id when given, else the alias.
    Zero organizations returns (None, None); more than one is an error: one organization is active per token.
    """
    if claim in (None, "", [], {}):
        return None, None
    if isinstance(claim, str):
        return claim, claim
    entries: list[tuple[str, str]]
    if isinstance(claim, dict):
        entries = []
        for alias, detail in claim.items():
            org_id = detail.get("id") if isinstance(detail, dict) else None
            entries.append((str(alias), str(org_id) if org_id else str(alias)))
    elif isinstance(claim, list) and all(isinstance(a, str) and a for a in claim):
        entries = [(a, a) for a in claim]
    else:
        raise OrganizationError("The organization claim is malformed")
    if len(entries) != 1:
        raise AmbiguousOrganization(
            "The token names several organizations; exactly one must be active"
        )
    alias, tenant = entries[0]
    return tenant, alias


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    return str(value) if value else None


def _actor(claims: Mapping[str, Any]) -> str | None:
    act = claims.get("act")
    return str(act["sub"]) if isinstance(act, dict) and act.get("sub") else None


def _token_scopes(claims: Mapping[str, Any]) -> frozenset[str]:
    raw = claims.get("scope") or ""
    return frozenset(raw.split()) if isinstance(raw, str) else frozenset()


def _context(claims: Mapping[str, Any]) -> dict[str, str]:
    context = {k: str(claims[k]) for k in CONTEXT_CLAIMS if claims.get(k)}
    if claims.get("jti"):
        context["token_id"] = str(claims["jti"])
    return context


__all__ = [
    "IdentityProviderUnavailable",
    "PrimaryIssuer",
    "TokenVerifier",
    "TrustedIssuer",
    "organization_of",
]

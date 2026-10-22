"""Validate access tokens: a thin adapter from `loamz.server.identity` to this service's `Principal`.

The primary OIDC provider's tokens and explicitly trusted issuers' tokens are verified by the shared
library (JWKS cache, rotation, stale-if-error, audience and `typ` checks). This module only decides what a
verified identity means here: administrator by group, read-only by scope, and the audit context.
"""

from __future__ import annotations

from typing import Any

import jwt

from loamz.core import tenancy
from loamz.engine.errors import UnauthenticatedError
from loamz.server.data.kernel.principal import Principal
from loamz.server.data.kernel.settings import Settings, TrustedIssuer
from loamz.server.identity import PrimaryIssuer
from loamz.server.identity import (
    TokenVerifier as IdentityVerifier,
)
from loamz.server.identity import TrustedIssuer as LibraryTrustedIssuer


def _with_tenant(who) -> frozenset[str]:
    """The caller's groups plus its tenant's group (`tenancy.org_group`), which tenant provisioning grants the
    tenant's workspace to. Derived from the verified ``tenant_id``, and any ``org:`` group the token itself names is
    dropped, so a token cannot claim another tenant."""
    groups = {g for g in who.groups if not g.startswith("org:")}
    if who.tenant_id:
        groups.add(tenancy.org_group(who.tenant_id))
    return frozenset(groups)


class _InjectedKeys:
    """Adapts a PyJWKClient-like object (tests, custom key sources) to the library's key source."""

    def __init__(self, jwks: Any):
        self.jwks = jwks

    def key_for(self, token: str) -> Any:
        return self.jwks.get_signing_key_from_jwt(token).key


def _trusted(config: TrustedIssuer) -> LibraryTrustedIssuer:
    return LibraryTrustedIssuer(
        issuer=config.issuer,
        audience=config.audience,
        jwks_uri=config.jwks_uri,
        algorithms=tuple(config.algorithms),
        jwks_ca_file=config.jwks_ca_file,
        allowed_scopes=frozenset(config.allowed_scopes)
        if config.allowed_scopes is not None
        else None,
        username_claim=config.username_claim,
        group_claim=config.group_claim,
    )


class TokenVerifier:
    def __init__(self, settings: Settings, jwks: Any | None = None):
        self.settings = settings
        self.trusted = {t.issuer: t for t in settings.trusted_issuers}
        self.verifier = IdentityVerifier(
            PrimaryIssuer(
                settings.oidc_issuer,
                settings.oidc_audience,
                jwks_url=settings.jwks_url,
            ),
            [_trusted(t) for t in settings.trusted_issuers],
            # Settings already require https off loopback for every URL, so plain loopback http is deliberate.
            insecure_http=True,
            # Organizations (tenants) are adopted per service; until the realm sends the claim this is optional.
            require_organization=False,
            primary_keys=_InjectedKeys(jwks) if jwks is not None else None,  # type: ignore[arg-type]
        )

    def verify(self, authorization: str | None) -> Principal:
        try:
            who = self.verifier.verify(authorization)
        except jwt.PyJWTError as exc:  # defensive: the library maps these itself
            raise UnauthenticatedError(f"Access token rejected: {type(exc).__name__}") from None
        if who.trusted_issuer:
            config = self.trusted[who.issuer]
            return Principal(
                subject=who.subject,
                name=who.name,
                kind="user",
                groups=_with_tenant(who),
                email=who.email,
                actor=who.actor,
                is_admin=False,  # a trusted issuer can never mint an administrator
                read_only=config.read_only or who.token_scopes == {"data:read"},
                scopes=who.scopes,
                tenant_id=who.tenant_id or None,
                context=who.context,
            )
        return Principal(
            subject=who.subject,
            name=who.name,
            kind=who.kind,
            groups=_with_tenant(who),
            roles=who.roles,
            email=who.email,
            actor=who.actor,
            is_admin=self.settings.admin_group in who.groups,
            read_only=who.token_scopes == {"data:read"},
            scopes=who.scopes,
            tenant_id=who.tenant_id or None,
            context=who.context,
        )

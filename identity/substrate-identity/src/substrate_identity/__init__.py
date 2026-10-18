"""Verify Keycloak access tokens (and tokens of explicitly trusted issuers) for every Substrate service."""

from substrate_identity.errors import (
    AmbiguousOrganization,
    Forbidden,
    IdentityError,
    IdentityProviderUnavailable,
    InsufficientScope,
    InvalidToken,
    MissingToken,
    OrganizationError,
    UntrustedIssuer,
)
from substrate_identity.identity import Identity
from substrate_identity.jwks import ALLOWED_ALGORITHMS, JwksSource
from substrate_identity.verifier import (
    PrimaryIssuer,
    TokenVerifier,
    TrustedIssuer,
    organization_of,
)

__all__ = [
    "ALLOWED_ALGORITHMS",
    "AmbiguousOrganization",
    "Forbidden",
    "Identity",
    "IdentityError",
    "IdentityProviderUnavailable",
    "InsufficientScope",
    "InvalidToken",
    "JwksSource",
    "MissingToken",
    "OrganizationError",
    "PrimaryIssuer",
    "TokenVerifier",
    "TrustedIssuer",
    "UntrustedIssuer",
    "organization_of",
]

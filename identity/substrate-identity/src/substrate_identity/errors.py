"""Typed verification failures. Each carries the HTTP status a service should answer with."""

from __future__ import annotations


class IdentityError(Exception):
    status = 401
    code = "unauthorized"

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class MissingToken(IdentityError):
    """No bearer token was presented."""


class InvalidToken(IdentityError):
    """Malformed, expired, wrong audience or issuer, bad signature, disallowed algorithm, not an access token."""


class UntrustedIssuer(InvalidToken):
    pass


class IdentityProviderUnavailable(IdentityError):
    """No usable signing keys: the issuer is unreachable and the cache is empty or older than the stale window."""

    status = 503
    code = "identity-provider-unavailable"


class OrganizationError(IdentityError):
    """The token does not name exactly one organization (tenant)."""

    status = 403
    code = "organization-required"


class AmbiguousOrganization(OrganizationError):
    code = "ambiguous-organization"


class InsufficientScope(IdentityError):
    status = 403
    code = "insufficient-scope"


class Forbidden(IdentityError):
    status = 403
    code = "forbidden"

"""FastAPI auth dependencies shared by the monolith and every microservice.

``get_current_user`` is the single entry point: it verifies the bearer token with ``substrate-identity`` (RS256 only, audience and
``typ`` checked, one organization) and returns the caller as :class:`AuthClaims`. Failures are typed ``IdentityError`` (401, 403
or 503) rendered as problem+json by ``install_problem_handlers``.
"""

from __future__ import annotations

from fastapi import Depends, Request
from substrate_identity import Forbidden, IdentityError, InsufficientScope

from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.auth.verifier import get_verifier


async def get_current_user(request: Request) -> AuthClaims:
    """Verify the bearer token. Raises 401 (missing or bad token), 403 (organization), 503 (no signing key)."""
    who = await get_verifier(request).averify(request.headers.get("authorization"))
    request.state.identity = who
    claims = AuthClaims.from_identity(who)
    if not claims.tenant_id and not claims.is_service:
        raise Forbidden("The token names no organization")
    return claims


async def optional_current_user(request: Request) -> AuthClaims | None:
    """The caller when a valid token is presented, else None (anonymous tier). A bad token is anonymous, not an error."""
    if not request.headers.get("authorization"):
        return None
    try:
        return await get_current_user(request)
    except IdentityError:
        return None


def require_role(*allowed_roles: str):
    """A dependency restricting a route to platform roles (``platform_admin``, ``tenant_admin``, ...)."""

    def checker(user: AuthClaims = Depends(get_current_user)) -> AuthClaims:
        if user.role not in allowed_roles:
            raise Forbidden(f"Role '{user.role}' is not authorized for this action")
        return user

    return checker


def require_scopes(*needed: str):
    """A dependency: an ordinary user token passes; a delegated token must hold at least one of ``needed``."""

    def checker(user: AuthClaims = Depends(get_current_user)) -> AuthClaims:
        if not user.allows_scope(*needed):
            raise InsufficientScope(f"This token needs the scope {' or '.join(needed)}")
        return user

    return checker


def require_service_identity(*roles: str):
    """A dependency: the caller must be a service account of the realm (client credentials), holding any of ``roles`` if given.

    Replaces the shared-secret service token: a platform service (cell-admin, the erasure job) signs in to Keycloak.
    """

    def checker(
        request: Request, user: AuthClaims = Depends(get_current_user)
    ) -> AuthClaims:
        if not user.is_service:
            raise Forbidden("This endpoint is for service accounts")
        if roles:
            held = request.state.identity.roles
            if not held & set(roles):
                raise Forbidden(f"This endpoint needs the role {' or '.join(roles)}")
        return user

    return checker

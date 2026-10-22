"""FastAPI / Starlette helpers (optional: needs `fastapi`, install `loamz-identity[fastapi]`).

verifier = TokenVerifier(PrimaryIssuer(issuer, "agents-api"), ...)
current = identity_dependency(verifier)          # or a callable taking the Request, to look it up lazily

@app.get("/me")
async def me(who: Identity = Depends(current)): ...

@app.post("/runs", dependencies=[Depends(require_roles(current, "tenant_admin", "end_user"))])
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request

from loamz.server.identity.errors import Forbidden, IdentityError, InsufficientScope
from loamz.server.identity.identity import Identity
from loamz.server.identity.verifier import TokenVerifier

IdentityDependency = Callable[[Request], Awaitable[Identity]]


def http_error(error: IdentityError) -> HTTPException:
    headers = {"WWW-Authenticate": 'Bearer realm="loamz"'} if error.status == 401 else None
    return HTTPException(
        error.status, detail={"code": error.code, "detail": error.detail}, headers=headers
    )


def identity_dependency(
    verifier: TokenVerifier | Callable[[Request], TokenVerifier],
) -> IdentityDependency:
    """A dependency that verifies the bearer token without blocking the event loop.

    The identity is also stored on `request.state.identity` for middleware and logging.
    """

    async def current(request: Request) -> Identity:
        active = verifier if isinstance(verifier, TokenVerifier) else verifier(request)
        try:
            who = await active.averify(request.headers.get("authorization"))
        except IdentityError as error:
            raise http_error(error) from None
        request.state.identity = who
        return who

    return current


def require_roles(current: IdentityDependency, *roles: str):
    """A dependency: the caller must hold at least one of `roles` (realm roles or the audience client's roles)."""
    from fastapi import Depends

    async def check(who: Identity = Depends(current)) -> Identity:
        if not who.has_role(*roles):
            raise http_error(Forbidden(f"This action needs the role {' or '.join(roles)}"))
        return who

    return check


def require_scopes(current: IdentityDependency, *needed: str):
    """A dependency: an ordinary user token passes; a delegated token must hold at least one of `needed`."""
    from fastapi import Depends

    async def check(who: Identity = Depends(current)) -> Identity:
        if not who.allows_scope(*needed):
            raise http_error(InsufficientScope(f"This token needs the scope {' or '.join(needed)}"))
        return who

    return check

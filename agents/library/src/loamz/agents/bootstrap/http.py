"""FastAPI dependencies every feature router uses: the container, the verified caller, and a feature's service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Request

from loamz.agents.bootstrap.container import Container, Provider
from loamz.agents.kernel.caller import Caller
from loamz.data.storage.errors import ForbiddenError, UnauthenticatedError
from loamz.identity import TokenVerifier


def container(request: Request) -> Container:
    return request.app.state.container


async def caller(request: Request) -> Caller:
    verifier: TokenVerifier | None = request.app.state.verifier
    if verifier is None:
        raise UnauthenticatedError(
            "Sign-in is not configured on this server (LOAMZ_AGENTS_OIDC__ISSUER)."
        )
    who = await verifier.averify(request.headers.get("authorization"))
    if not who.tenant_id:
        raise ForbiddenError("This token names no organization; choose one when signing in.")
    return Caller(
        tenant_id=who.tenant_id,
        user_id=who.subject,
        kind=who.kind,
        roles=who.roles,
        scopes=who.scopes,
        email=who.email,
        name=who.name,
    )


def use[T](provider: Provider[T]) -> Callable[..., Any]:
    """A dependency that resolves a feature's service: `Annotated[PreferencesService, Depends(use(preferences))]`."""

    def resolve(c: Container = Depends(container)) -> T:
        return c.get(provider)

    return resolve


ContainerDep = Annotated[Container, Depends(container)]
CallerDep = Annotated[Caller, Depends(caller)]

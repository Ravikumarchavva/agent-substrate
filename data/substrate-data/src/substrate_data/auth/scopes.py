"""Scopes: what a delegated token (an agent run acting for a user) may do.

An ordinary user token has `scopes=None` and is limited by grants alone. A delegated token carries
scopes such as `data:read`, `files:read`, `files:write`, `documents:read`, `documents:write`,
`memory:read`, `memory:write`. It may use only routes that declare the scope they need:

    @router.post("/volumes/{v}/files:sign", dependencies=[require_scope("files:write")])

A route that declares nothing is closed to delegated tokens (apart from the read-only statement and
browse allow-list for `data:read`), so a new mutation route cannot be reached by an agent by accident.
Grants still apply on top: a scope only lets the request in; it never grants access to data.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from fastapi.dependencies.models import Dependant

from substrate_data.auth.principal import Principal
from substrate_data.core.errors import Problem

SCOPES = (
    "data:read",
    "files:read",
    "files:write",
    "documents:read",
    "documents:write",
    "memory:read",
    "memory:write",
)


def require_scope(*needed: str):
    """A route dependency: a delegated token must hold at least one of `needed`."""
    from substrate_data.api.services import principal

    def check(request: Request, caller: Principal = Depends(principal)) -> Principal:
        if caller.scopes is not None and not (caller.scopes & set(needed)):
            raise Problem(
                403,
                "insufficient-scope",
                f"This token needs the scope {' or '.join(needed)}",
                required=list(needed),
            )
        return caller

    check.declared_scopes = frozenset(needed)  # type: ignore[attr-defined]
    return Depends(check)


def _walk(dependant: Dependant) -> Iterator[Dependant]:
    yield dependant
    for child in dependant.dependencies:
        yield from _walk(child)


def route_declares_scope(request: Request) -> bool:
    route = request.scope.get("route")
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    return any(getattr(d.call, "declared_scopes", None) for d in _walk(dependant))

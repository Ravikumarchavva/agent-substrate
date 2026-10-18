"""FastAPI dependencies every feature router uses: the container, the verified caller, a feature's service, paging."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Request

from loamz.data.server.kernel.container import Container, Provider
from loamz.data.server.kernel.errors import InsufficientScopeError
from loamz.data.server.kernel.principal import Principal
from loamz.data.server.kernel.scopes import route_declares_scope
from loamz.data.storage.errors import ForbiddenError, InvalidRequestError

MAX_PAGE = 1000


def container(request: Request) -> Container:
    return request.app.state.container


def principal(request: Request) -> Principal:
    """Authenticate every API request; the principal is cached on the request."""
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    services: Container = request.app.state.container
    value = services.verifier.verify(request.headers.get("authorization"))
    if value.read_only and not read_only_allowed(request):
        raise ForbiddenError("This token may read data but not change anything")
    if (
        value.scopes is not None
        and not value.read_only
        and not read_only_allowed(request)
        and not route_declares_scope(request)
    ):
        raise InsufficientScopeError("This token may not use this route", required=[])
    services.limiter.check(value.id)
    request.state.principal = value
    return value


def read_only_allowed(request: Request) -> bool:
    """A read-only token may browse and run statements (a POST that only reads)."""
    if request.method in {"GET", "HEAD"}:
        return True
    path = request.url.path
    return request.method == "POST" and (
        path == "/api/v1/statements"
        or (path.startswith("/api/v1/statements/") and path.endswith(":cancel"))
    )


def use[T](provider: Provider[T]) -> Callable[..., Any]:
    """A dependency resolving a feature's service: `Annotated[FilesService, Depends(use(files_service))]`."""

    def resolve(c: Container = Depends(container)) -> T:
        return c.get(provider)

    return resolve


def page_size(value: int | None) -> int:
    if value is None:
        return 100
    if not 1 <= value <= MAX_PAGE:
        raise InvalidRequestError(f"page_size must be between 1 and {MAX_PAGE}")
    return value


def encode_token(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def decode_token(token: str | None) -> str:
    if not token:
        return ""
    try:
        return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    except ValueError:
        raise InvalidRequestError("Invalid page_token") from None


Caller = Annotated[Principal, Depends(principal)]
Svc = Annotated[Container, Depends(container)]

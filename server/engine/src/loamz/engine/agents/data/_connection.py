"""One pooled connection to Loamz Data for the files, document and memory adapters.

Identity is per call, not per connection: each operation names the tenant and user it acts for,
the platform's token provider mints a delegated token for that identity, and the token is bound
to the call by a contextvar (so concurrent runs share the pool and never a token).
"""

from __future__ import annotations

import contextlib
import contextvars
import inspect
from collections.abc import Awaitable, Callable, Iterator

import httpx

from loamz.data import AsyncDataClient
from loamz.engine.errors import ForbiddenError

TENANT_PREFIX = "tenants/"

type TokenProvider = Callable[[str, str | None], str | Awaitable[str]]
"""(tenant_id, user_id or None) -> bearer token with required scopes."""

_BEARER: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "loamz_data_bearer", default=None
)


class DataServiceConnection:
    """The pooled connection to the Loamz Data platform."""

    def __init__(
        self,
        url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 120,
    ) -> None:
        self.client = AsyncDataClient(
            url, credential=self._current, timeout=timeout, transport=transport
        )

    @staticmethod
    def _current() -> str:
        token = _BEARER.get()
        if token is None:
            raise RuntimeError("a Loamz Data call was made outside any adapter operation")
        return token

    @contextlib.contextmanager
    def bearer(self, token: str) -> Iterator[None]:
        reset = _BEARER.set(token)
        try:
            yield
        finally:
            _BEARER.reset(reset)

    async def aclose(self) -> None:
        await self.client.aclose()


async def mint(tokens: TokenProvider, tenant: str, user: str | None) -> str:
    """The token for one identity."""
    try:
        token = tokens(tenant, user)
        return await token if inspect.isawaitable(token) else token
    except Exception as exc:
        raise ForbiddenError(f"no data-service token for this identity: {exc}") from exc


def identity_of(collection: str) -> tuple[str, str | None]:
    """`tenants/t/users/u/...` -> `('t', 'u')`; `tenants/t/knowledge/...` -> `('t', None)`."""
    if not collection.startswith(TENANT_PREFIX):
        raise ValueError(
            f"invalid collection {collection!r}: names must start with 'tenants/<tenant>/'"
        )
    tenant, _, rest = collection[len(TENANT_PREFIX) :].partition("/")
    if not tenant:
        raise ValueError(f"invalid collection {collection!r}: tenant cannot be empty")
    parts = rest.split("/")
    if len(parts) >= 2 and parts[0] == "users":
        return tenant, parts[1]
    return tenant, None

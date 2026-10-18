"""One pooled connection to substrate-data for the document and memory adapters.

Identity is per call, not per connection: each operation names the tenant and user it acts for, the platform's token provider mints a
delegated token for that identity, and the token is bound to the call by a contextvar (so concurrent runs share the pool and never a token).
"""

from __future__ import annotations

import contextlib
import contextvars
import inspect
from collections.abc import Awaitable, Callable, Iterator

import httpx
from substrate_data_client import AsyncDataClient

TENANT_PREFIX = "tenants/"
USER_SEGMENT = "users"

DocumentTokens = Callable[[str, "str | None"], "str | Awaitable[str]"]
"""``(tenant_id, user_id or None) -> bearer``: a ``documents:read`` / ``documents:write`` token for that identity. ``user_id`` is ``None``
for a collection that belongs to the organisation (a knowledge base): the platform decides whom it is minted for."""

_BEARER: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "substrate_data_bearer", default=None
)


class DataServiceConnection:
    def __init__(
        self,
        url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 120,
    ) -> None:
        self.client = AsyncDataClient(
            url, token=self._current, timeout=timeout, transport=transport
        )

    @staticmethod
    def _current() -> str:
        token = _BEARER.get()
        if token is None:
            raise RuntimeError(
                "a substrate-data call was made outside any adapter operation"
            )
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


async def mint(tokens: DocumentTokens, tenant: str, user: str | None) -> str:
    token = tokens(tenant, user)
    return await token if inspect.isawaitable(token) else token


def identity_of(collection: str) -> tuple[str, str | None]:
    """``tenants/t/users/u/…`` -> ``("t", "u")``; ``tenants/t/knowledge/…`` -> ``("t", None)``."""
    if not collection.startswith(TENANT_PREFIX):
        raise ValueError(
            f"invalid collection {collection!r}: names start with 'tenants/<tenant>/'"
        )
    tenant, _, rest = collection[len(TENANT_PREFIX) :].partition("/")
    if not tenant:
        raise ValueError(f"invalid collection {collection!r}: no tenant")
    parts = rest.split("/")
    user = (
        parts[1] if len(parts) > 1 and parts[0] == USER_SEGMENT and parts[1] else None
    )
    return tenant, user

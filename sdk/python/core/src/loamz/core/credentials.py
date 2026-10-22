"""Credentials: how a client proves who it is. A credential supplies a bearer token, and the transport asks it for one
on every request, so a token that is refreshed, rotated or re-read from a file is always the current one.

`loamz.identity` has the credentials that talk to an identity provider (client secret, the default chain); this module
has only the protocol and the trivial ones, so a client library depends on nothing it does not use.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class TokenCredential(Protocol):
    """Anything that can produce a bearer token. `get_token` serves the sync clients, `aget_token` the async ones."""

    def get_token(self) -> str: ...

    async def aget_token(self) -> str: ...


TokenSource = str | Callable[[], str] | Callable[[], Awaitable[str]] | TokenCredential | None


class StaticTokenCredential:
    """A fixed token, or a function that returns one (called per request: a refreshed file or cache is honoured).

    An `async def` function works with the async client only."""

    def __init__(self, source: str | Callable[[], str] | Callable[[], Awaitable[str]]) -> None:
        self._source = source

    def get_token(self) -> str:
        if isinstance(self._source, str):
            return self._source
        value = self._source()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise TypeError("An async token source needs the async client (loamz.<service>.aio)")
        return value

    async def aget_token(self) -> str:
        if isinstance(self._source, str):
            return self._source
        value = self._source()
        return await value if inspect.isawaitable(value) else value


def as_credential(source: TokenSource) -> TokenCredential | None:
    """A credential for whatever the caller passed: nothing, a token, a function, or a credential already."""
    if source is None:
        return None
    if isinstance(source, TokenCredential):
        return source
    return StaticTokenCredential(source)  # type: ignore[arg-type]

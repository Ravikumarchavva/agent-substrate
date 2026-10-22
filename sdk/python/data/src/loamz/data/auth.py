"""Token sources: where a client gets its bearer token.

A `token` argument may be a string, a function returning one (called for every request, so a refreshed
file or cache is honoured), an `async def` function (async client only), or `ClientCredentials`.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

TokenSource = str | Callable[[], str] | Callable[[], Awaitable[str]] | None


class ClientCredentials:
    """OAuth 2.0 client-credentials token source, cached until shortly before it expires.

    Works as a plain callable (sync clients) and through `await source.aget()` (async clients)."""

    def __init__(self, issuer: str, client_id: str, client_secret: str, scope: str | None = None):
        self.url = issuer.rstrip("/") + "/protocol/openid-connect/token"
        self.form = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if scope:
            self.form["scope"] = scope
        self.lock = threading.Lock()
        self.alock: asyncio.Lock | None = None
        self.value: tuple[str, float] | None = None

    def _cached(self) -> str | None:
        if self.value and time.time() < self.value[1] - 30:
            return self.value[0]
        return None

    def _store(self, response: httpx.Response) -> str:
        response.raise_for_status()
        body = response.json()
        self.value = (body["access_token"], time.time() + int(body.get("expires_in", 60)))
        return self.value[0]

    def __call__(self) -> str:
        with self.lock:
            return self._cached() or self._store(httpx.post(self.url, data=self.form, timeout=10))

    async def aget(self) -> str:
        if self.alock is None:
            self.alock = asyncio.Lock()
        async with self.alock:
            cached = self._cached()
            if cached:
                return cached
            async with httpx.AsyncClient(timeout=10) as http:
                return self._store(await http.post(self.url, data=self.form))


def current_token(source: Any) -> str | None:
    """The token now (sync). An async source cannot be used here."""
    if source is None or isinstance(source, str):
        return source
    value = source()
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError("An async token source needs AsyncDataClient")
    return value


async def current_token_async(source: Any) -> str | None:
    if source is None or isinstance(source, str):
        return source
    if isinstance(source, ClientCredentials):
        return await source.aget()
    value = source()
    return await value if inspect.isawaitable(value) else value

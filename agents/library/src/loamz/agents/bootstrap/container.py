"""Container: builds each dependency once, on first use, from `Settings`; closes them in reverse order.

It is the only composition root, and it knows no feature. A feature declares how its service is built (a provider:
a function of the container) next to the service, and anything that needs it asks `container.get(provider)`. So
infrastructure lives here, features stay self-contained, and dependencies only point downward.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import cached_property
from typing import Any

from loamz.agents.bootstrap.settings import Settings
from loamz.data.storage.sql import Database, postgres, sqlite

type Provider[T] = Callable[["Container"], T]


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._built: dict[Callable[..., Any], Any] = {}
        self._closers: list[Callable[[], Awaitable[None]]] = []

    def get[T](self, provider: Provider[T]) -> T:
        """The one instance `provider` builds for this container."""
        if provider not in self._built:
            self._built[provider] = provider(self)
        return self._built[provider]

    def on_close(self, closer: Callable[[], Awaitable[None]]) -> None:
        self._closers.append(closer)

    # ── infrastructure ──────────────────────────────────────────────────────
    @cached_property
    def database(self) -> Database:
        db = self.settings.database
        database = (
            postgres(
                db.url, schema=db.schema_name, pool_min_size=db.pool_min, pool_max_size=db.pool_max
            )
            if db.url
            else sqlite(db.path)
        )
        self.on_close(database.aclose)
        return database

    # ── lifecycle ───────────────────────────────────────────────────────────
    async def start(self) -> None:
        await self.database.start()

    async def aclose(self) -> None:
        while self._closers:
            await self._closers.pop()()

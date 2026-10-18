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
from loamz.agents.kernel.events import EventBus, InMemoryEventBus
from loamz.agents.kernel.models import ChatModel
from loamz.agents.kernel.ports.counters import Counters
from loamz.agents.kernel.ports.files import FileStore
from loamz.agents.kernel.runtime import Runtime
from loamz.agents.kernel.telemetry import set_capture_content
from loamz.agents.stores import AgentStore
from loamz.data.storage.sql import Database, postgres, sqlite

type Provider[T] = Callable[["Container"], T]


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        set_capture_content(settings.telemetry.capture_content)
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

    @cached_property
    def file_store(self) -> FileStore:
        """File bytes: a local folder (`files.path`) unless a data service is configured.

        TODO(integration): the [data] extra swaps in `loamz.agents.data.DataFileStore` (governed, versioned).
        """
        from loamz.agents.providers.files import LocalFileStore

        return LocalFileStore(self.settings.files.path)

    @cached_property
    def agent_store(self) -> AgentStore:
        """The agent's own state (threads and their headers, tasks, session state, graph, runs) in `database`."""
        return AgentStore(self.database, files=self.file_store)

    @cached_property
    def counters(self) -> Counters:
        """Rate-limit windows and daily quotas: shared through Redis when configured, otherwise this process's."""
        if not self.settings.redis.url:
            from loamz.agents.providers.limits.memory import MemoryCounters

            return MemoryCounters()
        import redis.asyncio as aioredis

        from loamz.agents.providers.limits.redis import RedisCounters

        client = aioredis.from_url(self.settings.redis.url, decode_responses=True)
        self.on_close(client.aclose)
        return RedisCounters(client)

    @cached_property
    def event_bus(self) -> EventBus:
        """Redis when `redis.url` is set (role-split deployments), otherwise one process's in-memory bus."""
        if not self.settings.redis.url:
            return InMemoryEventBus()
        from loamz.agents.providers.redis.bus import RedisEventBus

        bus = RedisEventBus(self.settings.redis.url)
        self.on_close(bus.disconnect)
        return bus

    def chat_model(self, model: str | None = None) -> ChatModel:
        """A client for `model` (the configured default when omitted), with the configured provider keys."""
        from loamz.agents.providers.llm import create_model_client

        llm = self.settings.llm
        return create_model_client(
            model or llm.model,
            api_keys=llm.api_keys(),
            openai_base_url=llm.openai_base_url or None,
        )

    @cached_property
    def runtime(self) -> Runtime:
        """The durable engine over `agent_store`. Its worker runs only in processes that serve runs (see `start`)."""
        return Runtime(
            self.agent_store,
            max_concurrency=self.settings.chat.max_concurrent_runs,
        )

    # ── lifecycle ───────────────────────────────────────────────────────────
    WORKER_ROLES = frozenset({"all", "runtime", "jobs"})

    async def start(self, roles: set[str] | frozenset[str] | None = None) -> None:
        """Open the database and bus. The runtime's worker starts only in a process whose roles execute runs; any other
        process still reads and signals runs through the shared store."""
        await self.database.start()
        await self.event_bus.connect()
        wanted = set(roles) if roles else set(self.settings.roles)
        if wanted & self.WORKER_ROLES:
            await self.runtime.start()
        else:
            await self.runtime.store.start()

    async def aclose(self) -> None:
        if "runtime" in self.__dict__:
            await self.runtime.stop()
        while self._closers:
            await self._closers.pop()()

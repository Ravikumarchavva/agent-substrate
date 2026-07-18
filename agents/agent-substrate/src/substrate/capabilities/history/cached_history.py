"""CachedHistoryProvider — fast cache in front of the real source of truth.

Replaces ``agents/factory.py::load_session_memory``'s side-channel
seed-on-miss function with a single object that satisfies ``HistoryProvider``
directly: any caller holding a ``history: HistoryProvider`` reference gets
correct cold-start behavior automatically, not just the specific call sites
that remembered to invoke a separate seeding step first.

This does NOT compose two ``HistoryProvider``s (a "primary" and a "cache").
In this codebase the actual durable source of truth for conversation history
is already the EventLogProtocol (monolith) or the ``conversation`` microservice —
written independently by the runtime's own step-logging, not through this
protocol's ``append()`` at all (see ``serving/stream/history.py`` and
``agents/factory.py::rebuild_messages_from_steps``). Modeling that as a
second peer ``HistoryProvider`` would mean two independently-written stores
claiming to be the source of truth for the same conversation. Instead,
``reseed`` is a callback that reconstructs the transcript from whichever
cold store already exists — the cache is the only thing this class writes.

Usage::

    history = CachedHistoryProvider(
        cache=RedisHistoryProvider(redis_url=redis_url),
        reseed=lambda: rebuild_messages_from_steps(
            await step_rows_from_log(runtime.event_log, runtime.scheduler, session_id),
            system_instructions,
        ),
        cold_store_name="EventLogProtocol",
    )
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from substrate.kernel.core.content import ChatMessage
from substrate.kernel.core.identity import AgentId
from substrate.kernel.storage.history import HistoryProvider
from substrate.logger import setup_logging

logger = setup_logging()


class CachedHistoryProvider:
    """``HistoryProvider`` that reads/writes a fast cache, self-healing once
    per cold session from a ``reseed`` callback.

    ``reseed``, when given, is called at most once per cold session — guarded
    by ``cache.try_acquire_seed_lock`` (when the cache exposes one, e.g.
    ``RedisHistoryProvider``) against two concurrent callers both observing
    the miss and double-seeding. It must return the full reconstructed
    transcript for the session. When ``reseed`` is ``None`` this is a thin
    passthrough to ``cache`` — appropriate for a session with no cold store
    to fall back to (e.g. a script or test with no runtime/EventLogProtocol).
    """

    def __init__(
        self,
        cache: HistoryProvider,
        *,
        reseed: Callable[[], Awaitable[list[ChatMessage]]] | None = None,
        cold_store_name: str = "cold store",
    ) -> None:
        self._cache = cache
        self._reseed = reseed
        self._cold_store_name = cold_store_name

    # -- HistoryProvider protocol ---------------------------------------------

    async def append(
        self,
        agent_id: AgentId,
        message: ChatMessage,
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        await self._cache.append(
            agent_id, message, session_id=session_id, run_id=run_id
        )

    async def append_many(
        self,
        agent_id: AgentId,
        messages: list[ChatMessage],
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        await self._cache.append_many(
            agent_id, messages, session_id=session_id, run_id=run_id
        )

    async def get_messages(
        self,
        agent_id: AgentId,
        *,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ChatMessage]:
        await self._ensure_seeded(agent_id, session_id=session_id)
        return await self._cache.get_messages(
            agent_id, session_id=session_id, limit=limit, offset=offset
        )

    async def clear(self, agent_id: AgentId, *, session_id: str) -> None:
        await self._cache.clear(agent_id, session_id=session_id)

    async def clear_run(
        self, agent_id: AgentId, *, session_id: str, run_id: str
    ) -> None:
        await self._cache.clear_run(agent_id, session_id=session_id, run_id=run_id)

    async def count_messages(self, agent_id: AgentId, *, session_id: str) -> int:
        await self._ensure_seeded(agent_id, session_id=session_id)
        return await self._cache.count_messages(agent_id, session_id=session_id)

    # -- cold-store reseed ------------------------------------------------------

    async def _ensure_seeded(self, agent_id: AgentId, *, session_id: str) -> None:
        if self._reseed is None:
            return
        if await self._cache.count_messages(agent_id, session_id=session_id) > 0:
            return

        acquire_lock = getattr(self._cache, "try_acquire_seed_lock", None)
        if acquire_lock is not None and not await acquire_lock(agent_id, session_id):
            # Lost the race -- someone else is seeding. Wait for their write
            # to land instead of double-seeding (a double-seed silently
            # truncates older messages once the cache's per-session cap
            # kicks in — see RedisHistoryProvider.try_acquire_seed_lock).
            for _ in range(50):
                if (
                    await self._cache.count_messages(agent_id, session_id=session_id)
                    > 0
                ):
                    return
                await asyncio.sleep(0.1)
            logger.warning(
                "Timed out waiting for concurrent seed of session %s", session_id
            )
            return

        messages = await self._reseed()
        if messages:
            await self._cache.append_many(
                agent_id, messages, session_id=session_id, run_id="cold_store"
            )
            logger.debug(
                "Seeded session %s with %d messages from %s",
                session_id,
                len(messages),
                self._cold_store_name,
            )

    def __repr__(self) -> str:
        return (
            f"<CachedHistoryProvider(cache={self._cache!r}, "
            f"reseed={'set' if self._reseed else None})>"
        )


__all__ = ["CachedHistoryProvider"]

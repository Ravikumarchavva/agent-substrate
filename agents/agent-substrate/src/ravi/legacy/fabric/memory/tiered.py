"""TieredHistoryProvider — a cached tier fronting a persistent tier.

Composes a fast volatile cache (e.g. Redis) in front of a durable store
(e.g. Postgres):

  - ``save_messages``  → write to cache; checkpoint to the store every N writes.
  - ``load_messages``  → serve from cache; on a cold cache, read the store and
                          warm the cache.
  - ``checkpoint``     → flush the cache's current view of a session to the store.

This is the generic write-through tier that replaces the old hardcoded
``SessionManager``.  Construct it as ``cached fronts persistent``::

    provider = TieredHistoryProvider(
        cache=RedisHistoryProvider(ttl=3600),
        store=PostgresHistoryProvider(database_url=...),
    )
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ravi.kernel.memory.history_provider import (
    CachedHistoryProvider,
    HistoryProvider,
    PersistentHistoryProvider,
)
from ravi.kernel.messages.base_message import BaseClientMessage
from ravi.logger import setup_logging

logger = setup_logging()


class TieredHistoryProvider(HistoryProvider):
    """Write-through cache (hot) over a durable store (cold).

    Parameters
    ----------
    cache:
        The fast, volatile tier (a :class:`CachedHistoryProvider`).
    store:
        The durable tier (a :class:`PersistentHistoryProvider`).
    checkpoint_every:
        Flush the cache to the store after this many new messages per session.
        ``0`` disables automatic checkpointing (call :meth:`checkpoint` manually).
    """

    def __init__(
        self,
        *,
        cache: CachedHistoryProvider,
        store: PersistentHistoryProvider,
        checkpoint_every: int = 50,
    ) -> None:
        if checkpoint_every < 0:
            raise ValueError("checkpoint_every must be >= 0")
        self._cache = cache
        self._store = store
        self._checkpoint_every = checkpoint_every
        self._dirty: Dict[str, int] = {}

    @property
    def cache(self) -> CachedHistoryProvider:
        return self._cache

    @property
    def store(self) -> PersistentHistoryProvider:
        return self._store

    # -- Lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        await self._cache.connect()
        await self._store.connect()
        logger.info("TieredHistoryProvider connected (cache + store)")

    async def disconnect(self) -> None:
        await self._cache.disconnect()
        await self._store.disconnect()
        logger.info("TieredHistoryProvider disconnected")

    # -- HistoryProvider contract ---------------------------------------------

    async def save_messages(
        self, session_id: str, messages: List[BaseClientMessage]
    ) -> int:
        if not messages:
            return 0
        written = await self._cache.save_messages(session_id, messages)
        dirty = self._dirty.get(session_id, 0) + written
        self._dirty[session_id] = dirty
        if self._checkpoint_every > 0 and dirty >= self._checkpoint_every:
            await self.checkpoint(session_id)
        return written

    async def load_messages(
        self, session_id: str, *, limit: Optional[int] = None
    ) -> List[BaseClientMessage]:
        if await self._cache.count_messages(session_id) > 0:
            return await self._cache.load_messages(session_id, limit=limit)
        # Cold cache — fall through to the durable store and warm the cache.
        stored = await self._store.load_messages(session_id)
        if stored:
            await self._cache.save_messages(session_id, stored)
        if limit is not None and limit > 0:
            return stored[-limit:]
        return stored

    async def count_messages(self, session_id: str) -> int:
        cached = await self._cache.count_messages(session_id)
        if cached > 0:
            return cached
        return await self._store.count_messages(session_id)

    async def clear_session(self, session_id: str) -> None:
        await self._cache.clear_session(session_id)
        await self._store.clear_session(session_id)
        self._dirty.pop(session_id, None)

    # -- Checkpointing --------------------------------------------------------

    async def checkpoint(self, session_id: str) -> int:
        """Flush the cache's view of *session_id* to the durable store.

        Overwrite strategy: clears the store's copy and writes the full cache
        snapshot, keeping the store in sync with the authoritative cache.
        Returns the number of messages persisted.
        """
        messages = await self._cache.load_messages(session_id)
        if not messages:
            self._dirty[session_id] = 0
            return 0
        await self._store.clear_session(session_id)
        saved = await self._store.save_messages(session_id, messages)
        self._dirty[session_id] = 0
        logger.info(
            "TieredHistoryProvider checkpointed session %s: %d messages → store",
            session_id,
            saved,
        )
        return saved

    def __repr__(self) -> str:
        return (
            f"<TieredHistoryProvider(cache={self._cache!r}, "
            f"store={self._store!r}, checkpoint_every={self._checkpoint_every})>"
        )

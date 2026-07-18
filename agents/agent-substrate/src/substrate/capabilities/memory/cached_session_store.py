"""CachedShortTermMemory — durable-primary + fast-cache for session state.

Unlike ``CachedHistoryProvider`` (which fronts an existing, independently
written cold store — the EventLogProtocol), ``ShortTermMemory`` has no pre-existing
durable owner: before this module, ``RedisSessionStore`` was the only
implementation, standalone, with no durable backing at all. So this class
composes two real ``ShortTermMemory`` peers — a durable ``primary`` and a
fast ``cache`` — the way ``CachedModelClient`` composes an ``LLMClient`` and
a ``SemanticCache``: writes go to ``primary`` first (that's the durability
guarantee), then best-effort to ``cache``; reads check ``cache`` first and
fall back to ``primary`` on a miss, repopulating ``cache``.

Usage::

    memory = CachedShortTermMemory(
        primary=DurableSessionStore(database_url=db_url),
        cache=RedisSessionStore(redis_url=redis_url),
    )
"""

from __future__ import annotations

from typing import Any

from substrate.kernel.storage.memory import ShortTermMemory
from substrate.logger import setup_logging

logger = setup_logging()


class CachedShortTermMemory:
    """``ShortTermMemory`` that writes durable-first, reads cache-first."""

    def __init__(self, primary: ShortTermMemory, cache: ShortTermMemory) -> None:
        self._primary = primary
        self._cache = cache

    async def get_state(self, session_id: str) -> dict[str, Any]:
        state = await self._cache.get_state(session_id)
        if state:
            return state
        state = await self._primary.get_state(session_id)
        if state:
            try:
                await self._cache.set_state(session_id, state)
            except Exception as exc:
                logger.warning(
                    "CachedShortTermMemory: cache repopulate failed for %s: %s",
                    session_id,
                    exc,
                )
        return state

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        await self._primary.set_state(session_id, state)
        try:
            await self._cache.set_state(session_id, state)
        except Exception as exc:
            logger.warning(
                "CachedShortTermMemory: cache write failed for %s: %s", session_id, exc
            )

    async def update_state(self, session_id: str, patch: dict[str, Any]) -> None:
        await self._primary.update_state(session_id, patch)
        try:
            await self._cache.update_state(session_id, patch)
        except Exception as exc:
            logger.warning(
                "CachedShortTermMemory: cache update failed for %s: %s", session_id, exc
            )

    async def clear(self, session_id: str) -> None:
        await self._primary.clear(session_id)
        try:
            await self._cache.clear(session_id)
        except Exception as exc:
            logger.warning(
                "CachedShortTermMemory: cache clear failed for %s: %s", session_id, exc
            )

    async def disconnect(self) -> None:
        """Disconnect both backends, best-effort (mirrors the individual
        stores' own connect/disconnect lifecycle for symmetric shutdown)."""
        for store in (self._primary, self._cache):
            disconnect = getattr(store, "disconnect", None)
            if disconnect is not None:
                await disconnect()

    def __repr__(self) -> str:
        return (
            f"<CachedShortTermMemory(primary={self._primary!r}, cache={self._cache!r})>"
        )


__all__ = ["CachedShortTermMemory"]

"""InMemorySessionStore — dict-backed ShortTermMemory for testing and single-process use."""

from __future__ import annotations

from typing import Any


class InMemorySessionStore:
    """ShortTermMemory backed by a plain Python dict.

    State is lost when the process exits.  Use for tests, notebooks, and
    single-process deployments where durability is not required.

    Usage::

        store = InMemorySessionStore()
        await store.update_state("sess-123", {"preferred_language": "Python"})
        state = await store.get_state("sess-123")
        # {"preferred_language": "Python"}
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def get_state(self, session_id: str) -> dict[str, Any]:
        return dict(self._store.get(session_id, {}))

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        self._store[session_id] = dict(state)

    async def update_state(self, session_id: str, patch: dict[str, Any]) -> None:
        self._store.setdefault(session_id, {}).update(patch)

    async def clear(self, session_id: str) -> None:
        self._store.pop(session_id, None)


__all__ = ["InMemorySessionStore"]

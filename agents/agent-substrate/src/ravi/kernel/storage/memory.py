"""Memory contracts — short-term and long-term memory for agents.

Two distinct memory scopes:

    ShortTermMemory   — key-value state within a single conversation session.
                        Lives as long as the session.
                        Backed by: Redis HASH, Postgres JSONB, in-memory dict.

    LongTermMemory    — extracted facts that persist across sessions forever.
                        "The user prefers Python", "User's name is Ravi".
                        Backed by: Postgres full-text, vector store (semantic),
                                   graph store (entity/relationship traversal),
                                   or hybrid.

Relationship to other kernel types:

    HistoryProvider   — the raw ordered message log (what was said).
    ShortTermMemory   — key-value facts learned during this session.
    LongTermMemory    — key facts extracted from past sessions.

Tenancy:
    Both protocols scope keys by ``namespace`` (tenant) + ``agent_id`` so
    multi-tenant deployments do not bleed facts across tenants.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ravi.kernel.core.identity import AgentId


@dataclass(frozen=True)
class Memory:
    """A single remembered fact returned from a search.

    ``content``  — the human-readable fact text.
    ``metadata`` — arbitrary tags: source session_id, timestamps, topic, etc.
    ``score``    — relevance to the search query (0–1); 0 when not ranked.
    ``id``       — stable identifier for deletion.
    """

    content: str
    score: float = 0.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ShortTermMemory — within one conversation session
# ---------------------------------------------------------------------------


class ShortTermMemory(Protocol):
    """Key-value state that persists across runs within one session.

    State is a flat ``dict[str, Any]`` — values must be JSON-serializable so
    any backend (Redis, Postgres, in-memory dict) can persist them.

    Concurrency: agents must use ``update_state`` (not get+set) for
    modifications so implementations can make the write atomic (e.g. Redis
    HSET writes only the patched keys; Postgres uses ``jsonb_set``).
    ``get_state``+``set_state`` is only for full replacement (onboarding,
    reset).
    """

    async def get_state(self, session_id: str) -> dict[str, Any]:
        """Return the full state dict for *session_id* (empty dict if absent)."""
        ...

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        """Replace the entire state for *session_id* with *state*."""
        ...

    async def update_state(self, session_id: str, patch: dict[str, Any]) -> None:
        """Atomically merge *patch* into existing state — other keys preserved."""
        ...

    async def clear(self, session_id: str) -> None:
        """Delete all state for *session_id*."""
        ...


# ---------------------------------------------------------------------------
# LongTermMemory — cross-session persistent facts
# ---------------------------------------------------------------------------


class LongTermMemory(Protocol):
    """Persistent facts extracted from conversations and retained forever.

    Facts are scoped to ``(namespace, agent_id)`` — each tenant+agent has its
    own memory namespace.  Implementations choose their own retrieval strategy.
    """

    async def save(
        self,
        agent_id: AgentId,
        content: str,
        *,
        namespace: str = "default",
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> str:
        """Persist *content* as a memory for *agent_id*.

        Returns the assigned ``memory_id`` so callers can delete it later.
        ``ttl_seconds`` sets optional expiry (``None`` = forever).
        """
        ...

    async def search(
        self,
        agent_id: AgentId,
        query: str,
        *,
        namespace: str = "default",
        limit: int = 10,
    ) -> list[Memory]:
        """Return up to *limit* memories most relevant to *query*."""
        ...

    async def get(
        self,
        agent_id: AgentId,
        memory_id: str,
        *,
        namespace: str = "default",
    ) -> Memory | None:
        """Return the memory with *memory_id*, or ``None`` if not found."""
        ...

    async def delete(
        self,
        agent_id: AgentId,
        memory_id: str,
        *,
        namespace: str = "default",
    ) -> bool:
        """Delete the memory with *memory_id*. Returns ``True`` if deleted."""
        ...

    async def clear(self, agent_id: AgentId, *, namespace: str = "default") -> None:
        """Delete all memories for *agent_id* in *namespace*."""
        ...


__all__ = ["Memory", "ShortTermMemory", "LongTermMemory"]

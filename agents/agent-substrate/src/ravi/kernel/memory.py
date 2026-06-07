"""Memory contracts — short-term and long-term memory for agents.

Two distinct memory scopes, both in one place:

    ShortTermMemory   — key-value state within a single conversation session.
                        Lives as long as the session.  Lost when the session ends.
                        Backed by: Redis HASH, Postgres JSONB, in-memory dict,
                                   vector store (semantic within-session recall),
                                   graph store (relational within-session state).

    LongTermMemory    — extracted facts that persist across sessions forever.
                        "The user prefers Python", "User's name is Ravi".
                        Backed by: Postgres full-text, vector store (semantic search),
                                   graph store (entity/relationship traversal),
                                   hybrid (vector + graph).

Relationship to other kernel types:

    HistoryProvider   — the raw ordered message log (what was said).
                        NOT memory — it is the conversation transcript.
    ShortTermMemory   — key-value facts learned during this session.
    LongTermMemory    — key facts extracted from past sessions.

Both protocols use ``search()`` as the retrieval interface so implementations
can be swapped freely:

    Postgres backend  — full-text / keyword search
    Vector backend    — embedding-based semantic similarity search
    Graph backend     — entity/relationship traversal
    Hybrid backend    — vector search + graph re-ranking
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ravi.kernel.identity import AgentId


# ---------------------------------------------------------------------------
# Shared value type
# ---------------------------------------------------------------------------


@dataclass
class Memory:
    """A single remembered fact returned from a search.

    ``content``  — the human-readable fact text.
    ``metadata`` — arbitrary tags: source session_id, timestamps, topic, etc.
    ``score``    — relevance to the search query (0–1); 0 when not ranked.
    ``id``       — stable identifier for deletion.
    """

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


# ---------------------------------------------------------------------------
# ShortTermMemory — within one conversation session
# ---------------------------------------------------------------------------


class ShortTermMemory(Protocol):
    """Key-value state that persists across runs within one session.

    State is a flat ``dict[str, Any]`` — values must be JSON-serializable so
    any backend (Redis, Postgres, vector DB) can persist them.

    Typical uses:
    - ``"preferred_language": "Python"``   set by a tool mid-conversation
    - ``"onboarding_complete": True``       written by agent, read next turn
    - ``"cart_id": "abc123"``              service-injected per session

    ``session_id`` is the conversation thread key — the same identifier used
    by ``HistoryProvider``.  One session_id maps to exactly one state dict.
    """

    async def get_state(self, session_id: str) -> dict[str, Any]:
        """Return the full state dict for *session_id*.

        Returns an empty dict if no state exists yet.
        """
        ...

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        """Replace the entire state for *session_id* with *state*."""
        ...

    async def update_state(self, session_id: str, patch: dict[str, Any]) -> None:
        """Merge *patch* into existing state — other keys are preserved.

        Implementations should make this atomic where the backend supports it
        (e.g. Redis ``HSET`` writes only the patched keys).
        """
        ...

    async def clear(self, session_id: str) -> None:
        """Delete all state for *session_id*."""
        ...


# ---------------------------------------------------------------------------
# LongTermMemory — cross-session persistent facts
# ---------------------------------------------------------------------------


class LongTermMemory(Protocol):
    """Persistent facts extracted from conversations and retained forever.

    Facts are scoped to an ``AgentId`` — each agent has its own memory
    namespace.  Implementations choose their own retrieval strategy:

    - ``PostgresMemoryStore``  — full-text / keyword search (tsvector)
    - ``VectorMemoryStore``    — semantic similarity via embeddings
    - ``GraphMemoryStore``     — entity/relationship traversal
    - ``HybridMemoryStore``    — vector retrieval + graph re-ranking

    The ``search()`` interface is the common surface all backends share.
    """

    async def save(
        self,
        agent_id: AgentId,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Persist *content* as a memory for *agent_id*.

        Returns the assigned ``memory_id`` so callers can delete it later.
        ``metadata`` can carry tags like ``{"session_id": "...", "topic": "..."}``.
        """
        ...

    async def search(
        self,
        agent_id: AgentId,
        query: str,
        *,
        limit: int = 10,
    ) -> list[Memory]:
        """Return up to *limit* memories most relevant to *query*.

        Relevance is backend-defined:
        - full-text:  ts_rank / BM25
        - vector:     cosine similarity of embeddings
        - graph:      relationship proximity to extracted query entities
        """
        ...

    async def get(self, agent_id: AgentId, memory_id: str) -> Memory | None:
        """Return the memory with *memory_id*, or ``None`` if not found."""
        ...

    async def delete(self, agent_id: AgentId, memory_id: str) -> bool:
        """Delete the memory with *memory_id*.  Returns ``True`` if deleted."""
        ...

    async def clear(self, agent_id: AgentId) -> None:
        """Delete all memories for *agent_id*."""
        ...


__all__ = ["Memory", "ShortTermMemory", "LongTermMemory"]

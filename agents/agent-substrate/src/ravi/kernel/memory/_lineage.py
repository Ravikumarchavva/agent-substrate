"""Memory lineage contracts — Section 9 (Memory + Graph Redesign).

Lineage answers: *who wrote this message, when, and why?*

Rather than bolting provenance onto the existing ``BaseMemory`` interface
(which would require every backend to be updated), lineage is a **parallel
store** keyed by ``(session_id, message_id)``.  The scheduler, trust plane,
and observability plane can all query lineage without touching message
storage.

``StorageTier`` formalises the Redis / Postgres / S3 tier split so
integration adapters can self-describe where they reside and the
``SessionManager`` can route accordingly.

Design constraints
------------------
* Zero concrete logic — this module holds only dataclasses + Protocols.
* No external imports — only stdlib.
* Implementations live in ``ravi.extensions.memory`` or
  ``ravi.integrations.memory``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol, Sequence, runtime_checkable

__all__ = [
    "StorageTier",
    "ProvenanceTag",
    "LineageRecord",
    "LineageNotFoundError",
    "LineageStore",
]


# ---------------------------------------------------------------------------
# Storage tier classification
# ---------------------------------------------------------------------------


class StorageTier(Enum):
    """Describes where a memory backend physically stores data.

    Used by ``SessionManager`` and routing logic to choose the right backend
    for hot vs warm vs cold data.
    """

    HOT = auto()
    """In-process or Redis: sub-millisecond reads.  Volatile."""

    WARM = auto()
    """PostgreSQL: durable, fast, fully queryable.  ~1–5 ms reads."""

    COLD = auto()
    """Object storage (S3, GCS, etc.): cheap, slow.  Minutes to restore."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProvenanceTag:
    """Captures the causal origin of one message write.

    Parameters
    ----------
    agent_fqn:
        Fully-qualified name of the agent that produced the message.
    activation_id:
        The :class:`ExecutionLease` activation ID under which the message
        was written.  Correlates to the economic plane's spend record.
    tool_call_id:
        Optional — the tool-call that triggered the message (for assistant
        messages that are tool results).
    parent_message_id:
        Optional — the ``message_id`` that causally precedes this one.
        Allows reconstruction of a causal chain (DAG) over the session.
    timestamp_utc:
        ISO-8601 UTC timestamp of the write.
    trust_score:
        Snapshot of the agent's trust score at write time.
        ``None`` when trust plane was not consulted (e.g., human messages).
    """

    agent_fqn: str
    activation_id: str
    timestamp_utc: str
    tool_call_id: str | None = None
    parent_message_id: str | None = None
    trust_score: float | None = None


@dataclass(frozen=True, slots=True)
class LineageRecord:
    """A single provenance record bound to a specific message.

    Parameters
    ----------
    session_id:
        The memory session this message belongs to.
    message_id:
        Stable identifier for the message.  Implementations must assign or
        derive this; the kernel does not mandate a format.
    provenance:
        The :class:`ProvenanceTag` describing origin.
    tier:
        Where the source message is currently stored.
    """

    session_id: str
    message_id: str
    provenance: ProvenanceTag
    tier: StorageTier = StorageTier.HOT


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class LineageNotFoundError(KeyError):
    """Raised when no lineage record exists for the requested key."""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LineageStore(Protocol):
    """Async provenance store — tracks the causal origin of every message.

    Implementations must be thread-safe.  Methods accepting ``session_id``
    must validate the ID against the same pattern used by ``RedisMemory``
    and ``PostgresMemory`` (alphanumeric + underscore/hyphen, 1–128 chars)
    to prevent injection attacks.
    """

    @property
    def tier(self) -> StorageTier:
        """Declare which storage tier backs this implementation."""
        ...

    async def record(
        self,
        session_id: str,
        message_id: str,
        provenance: ProvenanceTag,
    ) -> LineageRecord:
        """Persist a lineage record.

        Raises :class:`ValueError` on invalid ``session_id`` or
        ``message_id``.  Idempotent — recording the same ``message_id``
        twice overwrites the earlier record.
        """
        ...

    async def get(self, session_id: str, message_id: str) -> LineageRecord:
        """Fetch the lineage record for ``message_id`` in ``session_id``.

        Raises :class:`LineageNotFoundError` when no record exists.
        """
        ...

    async def list_session(
        self,
        session_id: str,
        *,
        limit: int | None = None,
    ) -> Sequence[LineageRecord]:
        """Return all lineage records for a session, oldest-first.

        ``limit`` caps the result count (``None`` = no cap).
        Raises :class:`ValueError` on invalid ``session_id``.
        """
        ...

    async def causal_chain(
        self,
        session_id: str,
        message_id: str,
    ) -> Sequence[LineageRecord]:
        """Follow ``parent_message_id`` links to return the full causal chain.

        The returned sequence starts with the root message (no parent) and
        ends with ``message_id``.  Raises :class:`LineageNotFoundError` if
        ``message_id`` has no record.  Cycles are detected and stop
        traversal immediately.
        """
        ...

    async def drop_session(self, session_id: str) -> None:
        """Delete all lineage records for a session.

        Raises :class:`ValueError` on invalid ``session_id``.
        """
        ...

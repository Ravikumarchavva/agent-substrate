"""Cognitive memory architecture contracts — short-term and multi-tier long-term memory.

Memory Taxonomy:
    DIRECTIVE   — Standing rules, user constraints, preferences (prioritized in prompt context).
    SEMANTIC    — Distilled facts, entity attributes, world knowledge (retrieved on-demand).
    EPISODIC    — Specific interaction records, tool execution logs, historical events.
    PROCEDURAL  — Reusable skill routines, execution heuristics, behavioral patterns.

Memory Scopes & Tenancy:
    MemoryNamespace scopes every record by `tenant_id` (mandatory isolation),
    with optional `user_id`, `agent_id`, and `session_id` sub-scoping.

Write Semantics:
    MemoryStore.save(record) performs an explicit upsert by record.id:
      - If record.id does not exist: INSERT.
      - If record.id exists: REPLACE.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from substrate.kernel.core.content import (
    ContentBlock,
    TextBlock,
    content_blocks_to_str,
)
from substrate.kernel.core.identity import Actor


class MemoryCategory(StrEnum):
    """Cognitive classification of memory records."""

    DIRECTIVE = "directive"    # Standing preferences, behavioral rules, immutable constraints
    SEMANTIC = "semantic"      # Distilled facts, world knowledge, entity properties
    EPISODIC = "episodic"      # Interaction records, tool traces, historical events
    PROCEDURAL = "procedural"  # Reusable workflows, tool heuristics, execution patterns


class MemoryStatus(StrEnum):
    """Lifecycle and consensus status of a memory record."""

    ACTIVE = "active"          # Verified, canonical memory ready for retrieval
    CANDIDATE = "candidate"    # Speculative or newly extracted memory awaiting promotion
    SUPERSEDED = "superseded"  # Replaced by newer or contradictory fact
    REJECTED = "rejected"      # Evaluated and discarded


@dataclass(frozen=True)
class MemoryNamespace:
    """Multi-tenant isolation and authority boundary.

    Query matching semantics:
      - tenant_id: Required exact match. Queries cannot cross tenant boundaries.
      - user_id: If specified, restricts to this user. If None, matches across users in tenant.
      - agent_id: If specified, restricts to this agent. If None, matches across agents in tenant.
      - session_id: If specified, restricts to this session. If None, matches cross-session memories.
    """

    tenant_id: str = "default"
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None

    @classmethod
    def from_actor(
        cls,
        actor: Actor,
        *,
        tenant_id: str = "default",
        session_id: str | None = None,
    ) -> MemoryNamespace:
        """Derive a namespace from a Kernel Actor identity."""
        if actor.type == "user":
            return cls(tenant_id=tenant_id, user_id=actor.key, session_id=session_id)
        if actor.type == "agent":
            return cls(tenant_id=tenant_id, agent_id=actor.key, session_id=session_id)
        return cls(tenant_id=tenant_id, agent_id=f"{actor.type}:{actor.key}", session_id=session_id)


@dataclass(frozen=True)
class MemoryProvenance:
    """Auditability, extraction attribution, and DAG branch awareness."""

    source_session_id: str | None = None
    source_node_id: str | None = None
    source_branch_id: str | None = None
    source_run_id: str | None = None
    confidence: float = 1.0
    extraction_method: str = "manual"  # e.g., "manual", "llm_reflection", "tool_output"
    supersedes_id: str | None = None   # Points to prior memory ID this record replaced


@dataclass(frozen=True)
class MemoryValidity:
    """Temporal validity — valid time when the real-world fact holds true."""

    valid_from: datetime | None = None
    valid_until: datetime | None = None


@dataclass(frozen=True)
class MemoryLifecycle:
    """Persistence and decay metadata."""

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed_at: datetime | None = None
    access_count: int = 0
    pinned: bool = False
    ttl_seconds: int | None = None


@dataclass(frozen=True)
class MemoryRecord:
    """Canonical immutable memory currency across Substrate.

    Score is intentionally excluded from this record because ranking is
    a property of a query, not the record's intrinsic identity.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    content: Sequence[ContentBlock] = field(default_factory=tuple)
    category: MemoryCategory = MemoryCategory.SEMANTIC
    status: MemoryStatus = MemoryStatus.ACTIVE
    namespace: MemoryNamespace = field(default_factory=lambda: MemoryNamespace(tenant_id="default"))
    provenance: MemoryProvenance = field(default_factory=MemoryProvenance)
    validity: MemoryValidity = field(default_factory=MemoryValidity)
    lifecycle: MemoryLifecycle = field(default_factory=MemoryLifecycle)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalize content to an immutable tuple of ContentBlocks
        if isinstance(self.content, str):
            object.__setattr__(self, "content", (TextBlock(text=self.content),))
        elif hasattr(self.content, "type"):  # Single ContentBlock instance
            object.__setattr__(self, "content", (self.content,))
        elif isinstance(self.content, (list, tuple)):
            object.__setattr__(self, "content", tuple(self.content))

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        id: str | None = None,
        category: MemoryCategory = MemoryCategory.SEMANTIC,
        status: MemoryStatus = MemoryStatus.ACTIVE,
        tenant_id: str = "default",
        user_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        namespace: MemoryNamespace | None = None,
        provenance: MemoryProvenance | None = None,
        validity: MemoryValidity | None = None,
        lifecycle: MemoryLifecycle | None = None,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> MemoryRecord:
        """Create a verified active text memory."""
        ns = namespace or MemoryNamespace(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        return cls(
            content=(TextBlock(text=text),),
            id=id or uuid.uuid4().hex,
            category=category,
            status=status,
            namespace=ns,
            provenance=provenance or MemoryProvenance(),
            validity=validity or MemoryValidity(),
            lifecycle=lifecycle or MemoryLifecycle(),
            metadata=metadata or {},
            **kwargs,
        )

    @classmethod
    def candidate(
        cls,
        content: str | ContentBlock | Sequence[ContentBlock],
        *,
        category: MemoryCategory = MemoryCategory.SEMANTIC,
        tenant_id: str = "default",
        user_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        namespace: MemoryNamespace | None = None,
        provenance: MemoryProvenance | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MemoryRecord:
        """Create a candidate memory record awaiting promotion/verification."""
        ns = namespace or MemoryNamespace(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        return cls(
            content=content,  # type: ignore[arg-type]
            category=category,
            status=MemoryStatus.CANDIDATE,
            namespace=ns,
            provenance=provenance or MemoryProvenance(),
            metadata=metadata or {},
        )

    def to_text(self) -> str:
        """Return human-readable text representation of all content blocks."""
        return content_blocks_to_str(self.content)

    @property
    def text(self) -> str:
        """Convenience string representation."""
        return self.to_text()

    def __str__(self) -> str:
        return self.to_text()


@dataclass(frozen=True)
class MemoryMatch:
    """Query result pairing a MemoryRecord with ephemeral query metrics."""

    record: MemoryRecord
    score: float
    rank: int = 0
    retrieval_method: str = "default"  # e.g., "vector", "fulltext", "hybrid", "exact"

    # Forwarding convenience properties
    @property
    def id(self) -> str:
        return self.record.id

    @property
    def content(self) -> Sequence[ContentBlock]:
        return self.record.content

    @property
    def category(self) -> MemoryCategory:
        return self.record.category

    @property
    def status(self) -> MemoryStatus:
        return self.record.status

    @property
    def namespace(self) -> MemoryNamespace:
        return self.record.namespace

    @property
    def metadata(self) -> Mapping[str, Any]:
        return self.record.metadata

    def to_text(self) -> str:
        return self.record.to_text()

    @property
    def text(self) -> str:
        return self.record.text

    def __str__(self) -> str:
        return self.record.to_text()


@dataclass(frozen=True)
class MemoryQuery:
    """Composable specification for searching memory stores."""

    namespace: MemoryNamespace
    text_query: str | None = None
    embedding: Sequence[float] | None = None
    categories: Sequence[MemoryCategory] | None = None
    statuses: Sequence[MemoryStatus] = (MemoryStatus.ACTIVE,)
    limit: int = 10
    min_score: float = 0.0
    include_pinned: bool = True
    metadata_filter: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ContextMemoryInjection:
    """Assembled memory context blocks ready for LLM context exposure."""

    directives: Sequence[ContentBlock] = field(default_factory=tuple)
    relevant_memories: Sequence[ContentBlock] = field(default_factory=tuple)
    estimated_tokens: int = 0


# ---------------------------------------------------------------------------
# ShortTermMemory — within one conversation session
# ---------------------------------------------------------------------------


@runtime_checkable
class ShortTermMemory(Protocol):
    """Key-value state that persists across runs within one session.

    State is a flat ``dict[str, Any]`` — values must be JSON-serializable so
    any durable backend (local filesystem, Redis, Postgres) can persist them.

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
# MemoryStore Protocol — canonical persistence contract
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryStore(Protocol):
    """The canonical durable memory storage contract.

    Write Semantics:
      - save(record): Explicit upsert by ID. If record.id does not exist, it is inserted;
        if record.id already exists, it is replaced.
    """

    async def save(self, record: MemoryRecord) -> str:
        """Persist or replace a record by its ID. Returns the record ID."""
        ...

    async def get(self, record_id: str) -> MemoryRecord | None:
        """Retrieve a specific record by ID."""
        ...

    async def delete(self, record_id: str) -> bool:
        """Permanently delete a record by ID. Returns True if deleted."""
        ...

    async def query(self, spec: MemoryQuery) -> list[MemoryMatch]:
        """Execute a search conforming to spec and return ranked matches."""
        ...

    async def touch(self, record_ids: Sequence[str]) -> None:
        """Record an access event (updates last_accessed_at and increments count)."""
        ...

    async def clear(self, namespace: MemoryNamespace) -> None:
        """Purge all records matching the given namespace boundary."""
        ...


__all__ = [
    "MemoryCategory",
    "MemoryStatus",
    "MemoryNamespace",
    "MemoryProvenance",
    "MemoryValidity",
    "MemoryLifecycle",
    "MemoryRecord",
    "MemoryMatch",
    "MemoryQuery",
    "ContextMemoryInjection",
    "ShortTermMemory",
    "MemoryStore",
]

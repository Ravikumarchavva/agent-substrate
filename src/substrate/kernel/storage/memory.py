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

Lifecycle signal (not enforced here):
    ``MemoryRecord.importance``/``last_accessed_at``/``access_count`` carry the
    recency/frequency/importance signal a future capabilities-layer memory
    consolidator needs to exist at all. Kernel only carries the fields —
    no decay or consolidation algorithm lives here (that belongs in
    ``capabilities/memory/``, same boundary as everything else memory-related).
    Nothing reads them yet; this is a deliberate, flagged trade-off, not an
    oversight — see the plan that introduced them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, Sequence, runtime_checkable

from pydantic import Field

from substrate.kernel.core.content import (
    BlockList,
    ContentBlock,
    JsonObject,
    KernelModel,
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


class ExtractionMethod(StrEnum):
    """How a memory record's content was produced."""

    MANUAL = "manual"                    # Directly authored (e.g. a stated user preference)
    LLM_REFLECTION = "llm_reflection"    # Distilled by an LLM reflecting on a conversation
    TOOL_OUTPUT = "tool_output"          # Captured verbatim from a tool execution result
    RULE_HEURISTIC = "rule_heuristic"    # Extracted by a deterministic rule, not an LLM
    USER_CORRECTION = "user_correction"  # A user explicitly corrected/superseded a memory


class MemoryNamespace(KernelModel):
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


class MemoryProvenance(KernelModel):
    """Auditability, extraction attribution, and DAG branch awareness."""

    source_session_id: str | None = None
    source_node_id: str | None = None
    source_branch_id: str | None = None
    source_run_id: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    extraction_method: ExtractionMethod = ExtractionMethod.MANUAL
    supersedes_id: str | None = None  # Points to prior memory ID this record replaced


class MemoryRecord(KernelModel):
    """Canonical immutable memory currency across Substrate.

    Score is intentionally excluded from this record because ranking is
    a property of a query, not the record's intrinsic identity.

    ``v`` is the entry schema version — bump only for a change old readers
    cannot handle (same rule as ``RunLogEntry.v``).
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    content: BlockList = Field(default_factory=list)
    category: MemoryCategory = MemoryCategory.SEMANTIC
    status: MemoryStatus = MemoryStatus.ACTIVE
    namespace: MemoryNamespace = Field(default_factory=lambda: MemoryNamespace(tenant_id="default"))
    provenance: MemoryProvenance = Field(default_factory=MemoryProvenance)
    metadata: JsonObject = Field(default_factory=dict)
    v: int = 1
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    last_accessed_at: datetime | None = None
    access_count: int = Field(default=0, ge=0)

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
        metadata: dict[str, Any] | None = None,
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
            content=[TextBlock(text=text)],
            id=id or uuid.uuid4().hex,
            category=category,
            status=status,
            namespace=ns,
            provenance=provenance or MemoryProvenance(),
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
        metadata: dict[str, Any] | None = None,
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


class MemoryMatch(KernelModel):
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
    def metadata(self) -> JsonObject:
        return self.record.metadata

    def to_text(self) -> str:
        return self.record.to_text()

    @property
    def text(self) -> str:
        return self.record.text

    def __str__(self) -> str:
        return self.record.to_text()


class MemoryQuery(KernelModel):
    """Composable specification for searching memory stores."""

    namespace: MemoryNamespace
    text_query: str | None = None
    embedding: Sequence[float] | None = None
    categories: Sequence[MemoryCategory] | None = None
    statuses: Sequence[MemoryStatus] = Field(default_factory=lambda: (MemoryStatus.ACTIVE,))
    limit: int = 10
    min_score: float = 0.0
    metadata_filter: JsonObject | None = None


class ContextMemoryInjection(KernelModel):
    """Assembled memory context blocks ready for LLM context exposure."""

    directives: Sequence[ContentBlock] = Field(default_factory=tuple)
    relevant_memories: Sequence[ContentBlock] = Field(default_factory=tuple)
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
    "ExtractionMethod",
    "MemoryNamespace",
    "MemoryProvenance",
    "MemoryRecord",
    "MemoryMatch",
    "MemoryQuery",
    "ContextMemoryInjection",
    "ShortTermMemory",
    "MemoryStore",
]

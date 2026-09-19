"""History storage contract — DAG-based MessageNode, Branch head pointers, and HistoryProvider protocol."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from pydantic import Field

from substrate.kernel.core.content import ChatMessage, JsonObject, KernelModel
from substrate.kernel.core.identity import Actor


class MessageNode(KernelModel):
    """An immutable node in the conversation DAG.

    Carries full execution provenance (session_id, run_id) alongside parent edges.
    ChatMessage payload contains purely message content and turn metadata.
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    parent_id: str | None = None  # None for session root node
    session_id: str
    run_id: str = ""
    payload: ChatMessage
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": True}


class Branch(KernelModel):
    """A named, movable pointer to a head node in the conversation DAG."""

    id: str                       # e.g., "main", "what-if-experiment"
    session_id: str
    head_message_id: str | None = None  # Current leaf message of this branch
    forked_from_message_id: str | None = None  # Ancestor node where this branch diverged
    version: int = 0              # Monotonically increasing version for optimistic concurrency
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": True}


class HistoryCheckpoint(KernelModel):
    """Immutable snapshot summarizing conversation ancestry up to an anchor node.

    Valid for any descendant branch that has anchor_message_id in its ancestry chain.
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str
    anchor_message_id: str        # Boundary node covered by this summary
    summary: str                  # Consolidated natural language / structured summary
    state: JsonObject = Field(default_factory=dict)
    parent_checkpoint_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": True}


class HistoryProvider(Protocol):
    """Durable DAG-based storage for an agent's conversation tree and branch heads."""

    # ── DAG Node & Branch Operations ──────────────────────────────────────────

    async def append_node(self, node: MessageNode) -> None:
        """Persist an immutable node.

        Invariants enforced:
        - Node ID is unique and cannot be overwritten.
        - Idempotent if node already exists with identical fields.
        - Node cannot reference itself (node.id != node.parent_id).
        - If parent_id is not None, parent node must exist in the same session.
        """
        ...

    async def get_node(self, node_id: str) -> MessageNode | None:
        """Fetch a single node by its ID."""
        ...

    async def get_branch(self, session_id: str, branch_id: str) -> Branch | None:
        """Fetch a branch head pointer by session and branch ID."""
        ...

    async def list_branches(self, session_id: str) -> list[Branch]:
        """List all branches stored for a session."""
        ...


    async def fork_branch(
        self,
        session_id: str,
        source_branch_id: str,
        new_branch_id: str,
        *,
        fork_from_message_id: str | None = None,
    ) -> Branch:
        """Atomically fork a new branch pointer from a source branch head or specific ancestor node.

        Invariants enforced:
        - source_branch_id must exist in session_id.
        - new_branch_id must not already exist in session_id.
        - If source_branch is empty and fork_from_message_id is None, creates an empty branch.
        - If fork_from_message_id is supplied, it must exist, belong to session_id,
          and be verified as an ancestor of source_branch's head.
        """
        ...

    async def set_branch_head(
        self,
        session_id: str,
        branch_id: str,
        new_head_id: str,
        *,
        expected_head_id: str | None = None,
        expected_version: int | None = None,
    ) -> Branch:
        """Advance branch head pointer using optimistic concurrency control.

        Raises BranchHeadConflictError if expected_head_id or expected_version does not match.
        """
        ...

    async def append_and_advance(
        self,
        node: MessageNode,
        branch_id: str,
        *,
        expected_head_id: str | None = None,
        expected_version: int | None = None,
    ) -> Branch:
        """Atomically append a node and advance the branch head to that node.

        Invariants enforced:
        - node.parent_id must match the current branch head (None for empty branch).
        - Concurrency check against expected_head_id / expected_version.
        """
        ...

    # ── Checkpoint Operations ────────────────────────────────────────────────

    async def save_checkpoint(self, checkpoint: HistoryCheckpoint) -> None:
        """Persist a compaction checkpoint anchor."""
        ...

    async def get_checkpoint(self, checkpoint_id: str) -> HistoryCheckpoint | None:
        """Fetch a checkpoint by ID."""
        ...

    async def list_checkpoints(self, session_id: str) -> list[HistoryCheckpoint]:
        """List all checkpoints stored for a session."""
        ...

    # ── Legacy / Linear Compatibility Methods ───────────────────────────────

    async def append(
        self,
        agent_id: Actor,
        message: ChatMessage,
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        """Append message to agent's history for session_id."""
        ...

    async def append_many(
        self,
        agent_id: Actor,
        messages: list[ChatMessage],
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        """Append multiple messages in one write."""
        ...

    async def get_messages(
        self,
        agent_id: Actor,
        *,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ChatMessage]:
        """Return the chronological message history."""
        ...

    async def clear(self, agent_id: Actor, *, session_id: str) -> None:
        """Delete all history for agent_id in session_id."""
        ...

    async def clear_run(
        self, agent_id: Actor, *, session_id: str, run_id: str
    ) -> None:
        """Delete messages belonging to run_id within session_id."""
        ...

    async def count_messages(self, agent_id: Actor, *, session_id: str) -> int:
        """Return the number of messages stored."""
        ...


class HistoryResolver(Protocol):
    """Encapsulates graph traversal over a HistoryProvider."""

    async def resolve_ancestry(
        self,
        leaf_message_id: str,
        *,
        stop_at_node_id: str | None = None,
    ) -> list[MessageNode]:
        """Walk parent_id edges from leaf back to root (or stop_at_node_id), returning chronological order."""
        ...


class CheckpointResolver(Protocol):
    """Locates checkpoints valid for a specific branch lineage."""

    async def find_applicable_checkpoint(
        self,
        leaf_message_id: str,
    ) -> HistoryCheckpoint | None:
        """Find nearest checkpoint whose anchor_message_id is an ancestor of leaf_message_id."""
        ...


__all__ = [
    "MessageNode",
    "Branch",
    "HistoryCheckpoint",
    "HistoryProvider",
    "HistoryResolver",
    "CheckpointResolver",
]

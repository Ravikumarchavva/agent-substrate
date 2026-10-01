"""History storage contract — the conversation DAG.

A session's history is a DAG of immutable ``MessageNode``s; a ``Branch`` is a
movable head pointer into it, and ``HistoryCheckpoint`` summarises ancestry up
to an anchor node. This is the *only* history model: a linear transcript is a
projection of one branch (``agents/context/history.py::project_messages``), not
a second store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import Field

from substrate.kernel.core.content import ChatMessage, JsonObject, KernelModel


class MessageNode(KernelModel):
    """An immutable node in the conversation DAG.

    Carries full execution provenance (session_id, run_id) alongside parent edges.
    ChatMessage payload contains purely message content and turn metadata.

    ``workspace_snapshot_id`` ties this node to the workspace state as of the
    turn that produced it (``kernel/storage/snapshots.py::WorkspaceSnapshot``)
    — one snapshot per turn, so forking the conversation at this node and
    forking its workspace are the same operation: read this field, call
    ``WorkspaceStore.fork_branch_snapshot``. ``None`` for nodes that predate
    workspace snapshotting or never touched a workspace (e.g. a root node).
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    parent_id: str | None = None  # None for session root node
    session_id: str
    run_id: str = ""
    payload: ChatMessage
    workspace_snapshot_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Branch(KernelModel):
    """A named, movable pointer to a head node in the conversation DAG."""

    id: str                       # e.g., "main", "what-if-experiment"
    session_id: str
    name: str | None = None       # Human-readable display name
    head_message_id: str | None = None  # Current leaf message of this branch
    forked_from_message_id: str | None = None  # Ancestor node where this branch diverged
    version: int = 0              # Monotonically increasing version for optimistic concurrency
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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


@runtime_checkable
class HistoryProvider(Protocol):
    """Durable storage for a session's conversation DAG, branch heads and checkpoints."""

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

    async def ensure_branch(
        self, session_id: str, branch_id: str, *, head_message_id: str | None = None
    ) -> Branch:
        """Fetch the branch, creating it (optionally at ``head_message_id``) if absent."""
        ...

    async def rename_branch(
        self, session_id: str, branch_id: str, new_name: str
    ) -> Branch:
        """Change a branch's display name. Raises BranchNotFoundError if absent."""
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

    async def delete_branch(self, session_id: str, branch_id: str) -> None:
        """Delete a branch pointer.

        Only the ``Branch`` record — the named head pointer — is removed;
        the underlying ``MessageNode``s it pointed into are left untouched
        (same as ``git branch -d``: deleting a branch never deletes
        commits, only the ref). They may still be reachable from another
        branch that shares the same ancestry, and reachability-based
        cleanup of genuinely orphaned nodes is a deliberately deferred GC
        concern, not this method's job.

        Raises ``ValueError`` for ``branch_id == "main"`` — every session's
        history assumes a main branch exists (``ensure_branch`` auto-creates
        it, routes default to it). Idempotent otherwise: deleting an
        unknown branch is a no-op.
        """
        ...

    # ── Session lifecycle ────────────────────────────────────────────────────

    async def delete_session(self, session_id: str) -> None:
        """Delete every node, branch and checkpoint of ``session_id``.

        Used for run-scoped history (``HistoryRetention.RUN``) and explicit
        resets. Idempotent: deleting an unknown session is a no-op.
        """
        ...


__all__ = [
    "MessageNode",
    "Branch",
    "HistoryCheckpoint",
    "HistoryProvider",
]

"""Hierarchical checkpointing — tree-structured snapshots for sub-agent recovery.

The old ``AgentCheckpoint`` was flat: one checkpoint per (run_id, agent_id).
Sub-agents spawned by an orchestrator were not tracked, so a crash mid-handoff
lost everything.

The new ``RunCheckpoint`` is a **tree**:

```
RunCheckpoint (root: orchestrator)
├── iteration: 3
├── children:
│   ├── RunCheckpoint (code_agent, status=completed, result=...)
│   └── RunCheckpoint (research_agent, status=in_progress ← crash)
├── resource_locks: [...]
└── pending_sagas: [...]
```

On recovery:
1. Load root checkpoint
2. For each child: if ``completed`` → use stored result; if ``in_progress`` → re-run
3. Restore resource locks and saga state
4. Resume parent at the iteration where it left off

Backward compatibility: the old ``AgentCheckpoint`` fields (run_id, agent_id,
iteration, messages) are preserved as properties on ``RunCheckpoint``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from ravi.core.messages.content import JsonObject


# ---------------------------------------------------------------------------
# Checkpoint status
# ---------------------------------------------------------------------------


class CheckpointStatus(str, Enum):
    """Lifecycle status of a checkpointed execution."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SUSPENDED = "suspended"  # paused by user or policy


# ---------------------------------------------------------------------------
# RunCheckpoint — the new tree-structured checkpoint
# ---------------------------------------------------------------------------


class RunCheckpoint(BaseModel):
    """Tree-structured checkpoint for hierarchical agent execution.

    Each checkpoint represents a single agent's execution state and links
    to its children (sub-agents it has delegated to).

    Fields:
        checkpoint_id: Unique ID for this checkpoint (auto-generated).
        run_id: The execution run ID (shared across the entire tree).
        agent_id: The agent's name/ID.
        parent_checkpoint_id: ID of the parent checkpoint (None for root).
        status: Current lifecycle status.
        iteration: Last completed iteration (for ReAct loops).
        messages: Serialised message history.
        result: Serialised final result (when completed).
        children: Child checkpoints for sub-agent executions.
        resource_locks: Serialised lock handles held at checkpoint time.
        pending_sagas: Serialised saga records in progress.
        metadata: Open-ended dict for agent-specific state.
        created_at: When this checkpoint was first created.
        updated_at: When this checkpoint was last updated.
    """

    checkpoint_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    agent_id: str
    parent_checkpoint_id: Optional[str] = None
    thread_id: str = ""
    status: CheckpointStatus = CheckpointStatus.NOT_STARTED
    iteration: int = 0
    messages: list[dict[str, Any]] = Field(default_factory=list)
    result: Optional[JsonObject] = None
    children: list["RunCheckpoint"] = Field(default_factory=list)
    resource_locks: list[dict[str, Any]] = Field(default_factory=list)
    pending_sagas: list[dict[str, Any]] = Field(default_factory=list)
    pending_tool_ids: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": False}

    # -- tree navigation ---------------------------------------------------

    def find_child(self, agent_id: str) -> Optional["RunCheckpoint"]:
        """Find a direct child checkpoint by agent_id."""
        for child in self.children:
            if child.agent_id == agent_id:
                return child
        return None

    def find_descendant(self, agent_id: str) -> Optional["RunCheckpoint"]:
        """DFS search for a checkpoint by agent_id anywhere in the tree."""
        if self.agent_id == agent_id:
            return self
        for child in self.children:
            found = child.find_descendant(agent_id)
            if found is not None:
                return found
        return None

    def add_child(self, child: "RunCheckpoint") -> None:
        """Add a child checkpoint (for a sub-agent execution)."""
        child.parent_checkpoint_id = self.checkpoint_id
        child.run_id = self.run_id
        self.children.append(child)

    # -- status helpers ----------------------------------------------------

    @property
    def is_complete(self) -> bool:
        return self.status == CheckpointStatus.COMPLETED

    @property
    def is_failed(self) -> bool:
        return self.status == CheckpointStatus.FAILED

    @property
    def is_in_progress(self) -> bool:
        return self.status == CheckpointStatus.IN_PROGRESS

    @property
    def needs_recovery(self) -> bool:
        """True if this or any child was in progress (interrupted)."""
        if self.status == CheckpointStatus.IN_PROGRESS:
            return True
        return any(child.needs_recovery for child in self.children)

    @property
    def incomplete_children(self) -> list["RunCheckpoint"]:
        """Return children that need to be re-run."""
        return [
            c for c in self.children
            if c.status in (CheckpointStatus.IN_PROGRESS, CheckpointStatus.NOT_STARTED)
        ]

    @property
    def completed_children(self) -> list["RunCheckpoint"]:
        """Return children that completed successfully."""
        return [c for c in self.children if c.is_complete]

    # -- update helpers ----------------------------------------------------

    def mark_in_progress(self, iteration: int = 0) -> None:
        """Mark this checkpoint as in progress."""
        self.status = CheckpointStatus.IN_PROGRESS
        self.iteration = iteration
        self.updated_at = datetime.now(timezone.utc)

    def mark_completed(self, result: JsonObject | None = None) -> None:
        """Mark this checkpoint as completed."""
        self.status = CheckpointStatus.COMPLETED
        self.result = result
        self.updated_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str = "") -> None:
        """Mark this checkpoint as failed."""
        self.status = CheckpointStatus.FAILED
        self.metadata["error"] = error
        self.updated_at = datetime.now(timezone.utc)

    def update_iteration(self, iteration: int, messages: list[dict[str, Any]] | None = None) -> None:
        """Update the iteration counter and optionally the message history."""
        self.iteration = iteration
        if messages is not None:
            self.messages = messages
        self.updated_at = datetime.now(timezone.utc)


# Rebuild for self-referential children field
RunCheckpoint.model_rebuild()


# ---------------------------------------------------------------------------
# CheckpointStore — abstract interface (rewritten)
# ---------------------------------------------------------------------------


class CheckpointStore(ABC):
    """Abstract store for hierarchical run checkpoints.

    Implementations should persist the entire tree atomically when ``save``
    is called — partial writes would break recovery.
    """

    @abstractmethod
    async def save(self, checkpoint: RunCheckpoint) -> None:
        """Persist the checkpoint tree (root + all descendants).

        Must be atomic: either the entire tree is written or nothing is.
        """
        ...

    @abstractmethod
    async def load(self, run_id: str, agent_id: str) -> Optional[RunCheckpoint]:
        """Load the most recent checkpoint tree for *run_id* + *agent_id*.

        Returns the root checkpoint with all children populated.
        """
        ...

    @abstractmethod
    async def load_tree(self, run_id: str) -> Optional[RunCheckpoint]:
        """Load the entire checkpoint tree for a run.

        Returns the root checkpoint.
        """
        ...

    @abstractmethod
    async def delete(self, run_id: str, agent_id: str) -> None:
        """Delete the checkpoint for *run_id* + *agent_id* and its children."""
        ...

    @abstractmethod
    async def list_runs(self) -> list[str]:
        """Return all run IDs that have checkpoints."""
        ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryCheckpointStore(CheckpointStore):
    """In-memory checkpoint store for single-process deployments and testing.

    Stores checkpoint trees keyed by (run_id, agent_id).
    """

    def __init__(self) -> None:
        self._store: dict[str, RunCheckpoint] = {}

    @staticmethod
    def _key(run_id: str, agent_id: str) -> str:
        return f"{run_id}:{agent_id}"

    async def save(self, checkpoint: RunCheckpoint) -> None:
        self._store[self._key(checkpoint.run_id, checkpoint.agent_id)] = checkpoint

    async def load(self, run_id: str, agent_id: str) -> Optional[RunCheckpoint]:
        return self._store.get(self._key(run_id, agent_id))

    async def load_tree(self, run_id: str) -> Optional[RunCheckpoint]:
        # Find root (no parent)
        for cp in self._store.values():
            if cp.run_id == run_id and cp.parent_checkpoint_id is None:
                return cp
        return None

    async def delete(self, run_id: str, agent_id: str) -> None:
        self._store.pop(self._key(run_id, agent_id), None)

    async def list_runs(self) -> list[str]:
        return list({cp.run_id for cp in self._store.values()})

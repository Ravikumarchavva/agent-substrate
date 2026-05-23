"""CheckpointStore — abstract interface and in-memory implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ravi.core.checkpointing.models import AgentCheckpoint


class CheckpointStore(ABC):
    """Abstract store for agent checkpoints.

    Implement this to persist checkpoints in Redis, PostgreSQL, or any other
    backend that suits your deployment.
    """

    @abstractmethod
    async def save(self, checkpoint: AgentCheckpoint) -> None:
        """Persist *checkpoint*, overwriting any previous checkpoint for the
        same (run_id, agent_id) pair."""
        ...

    @abstractmethod
    async def load(self, run_id: str, agent_id: str) -> Optional[AgentCheckpoint]:
        """Load the most recent checkpoint for *run_id* + *agent_id*.

        Returns None if no checkpoint exists.
        """
        ...

    @abstractmethod
    async def delete(self, run_id: str, agent_id: str) -> None:
        """Delete the checkpoint for *run_id* + *agent_id* if it exists."""
        ...

    @abstractmethod
    async def list_checkpoints(self, run_id: str) -> list[AgentCheckpoint]:
        """Return all checkpoints for *run_id* (across all agents in a run)."""
        ...


class InMemoryCheckpointStore(CheckpointStore):
    """In-memory checkpoint store — suitable for single-process deployments.

    Each (run_id, agent_id) key maps to the most recent checkpoint.
    """

    def __init__(self) -> None:
        self._store: dict[str, AgentCheckpoint] = {}

    @staticmethod
    def _key(run_id: str, agent_id: str) -> str:
        return f"{run_id}:{agent_id}"

    async def save(self, checkpoint: AgentCheckpoint) -> None:
        self._store[self._key(checkpoint.run_id, checkpoint.agent_id)] = checkpoint

    async def load(self, run_id: str, agent_id: str) -> Optional[AgentCheckpoint]:
        return self._store.get(self._key(run_id, agent_id))

    async def delete(self, run_id: str, agent_id: str) -> None:
        self._store.pop(self._key(run_id, agent_id), None)

    async def list_checkpoints(self, run_id: str) -> list[AgentCheckpoint]:
        prefix = f"{run_id}:"
        return [cp for key, cp in self._store.items() if key.startswith(prefix)]

"""Minimal CheckpointStore Protocol + in-memory default implementation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ravi.fabric.durable.checkpoint import FlowCheckpoint


@runtime_checkable
class CheckpointStore(Protocol):
    """Protocol for FlowCheckpoint persistence backends."""

    async def save(self, checkpoint: FlowCheckpoint) -> None:
        """Persist a checkpoint.  Overwrites any prior checkpoint for the same
        (run_id, flow_id) pair."""
        ...

    async def load(self, run_id: str, flow_id: str) -> FlowCheckpoint | None:
        """Return the latest checkpoint for (run_id, flow_id), or None."""
        ...


class InMemoryCheckpointStore:
    """Default store — no persistence across process restarts.

    Suitable for testing and single-process resumable flows.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], FlowCheckpoint] = {}

    async def save(self, checkpoint: FlowCheckpoint) -> None:
        self._store[(checkpoint.run_id, checkpoint.flow_id)] = checkpoint

    async def load(self, run_id: str, flow_id: str) -> FlowCheckpoint | None:
        return self._store.get((run_id, flow_id))

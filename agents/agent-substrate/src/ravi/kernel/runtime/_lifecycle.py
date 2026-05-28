"""Dormant agent lifecycle contracts for distributed runtimes.

These kernel-level ABCs/dataclasses/Protocols define the interface that any
distributed runtime must implement to manage dormant agents at planetary scale.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "AgentLifecycleState",
    "ActivationTrigger",
    "ExecutionLease",
    "CheckpointRef",
    "AgentActivationContract",
    "Checkpointable",
    "ActivationAware",
]


class AgentLifecycleState(Enum):
    """The activation lifecycle state of a logical agent."""

    DORMANT = auto()       # No compute allocated; state persisted in durable store
    ACTIVATING = auto()    # Being loaded/replayed — not yet ready
    ACTIVE = auto()        # Executing; lease held
    CHECKPOINTING = auto() # Mid-checkpoint write; compute still allocated
    HIBERNATING = auto()   # Draining work; will become DORMANT after flush
    SUSPENDED = auto()     # Paused by policy/quota/HITL; awaiting external unblock
    TERMINATED = auto()    # Final state; state may be archived or GC'd


@dataclass(frozen=True, slots=True)
class ActivationTrigger:
    """What caused an agent to transition from DORMANT → ACTIVATING."""

    trigger_type: str          # "message" | "schedule" | "wakeup" | "replay" | "resurrection"
    source_id: str             # Envelope id or schedule id that triggered this
    replayed: bool = False     # True when processing a historical event (replay mode)
    wakeup_key: str | None = None  # Idempotency key for deduplication


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    """Lease granting a worker the right to execute an agent activation."""

    agent_id_str: str          # AgentId.__str__() value
    worker_id: str             # Worker/pod that holds the lease
    lease_id: str              # Unique lease token
    granted_at: str            # ISO-8601
    expires_at: str            # ISO-8601 — worker must renew or release before this
    budget_tokens: int = 0     # Max token budget for this activation (0 = unlimited)
    budget_steps: int = 0      # Max tool-call steps (0 = unlimited)


@dataclass(frozen=True, slots=True)
class CheckpointRef:
    """Slim pointer into a persisted ``RunCheckpoint``.

    Carries everything a worker needs to load the full checkpoint tree
    from its ``CheckpointStore``: ``run_id`` + ``agent_id_str`` is the
    primary key, ``checkpoint_id`` identifies the specific snapshot, and
    ``store_uri`` indicates which backend holds it.

    The ``RunCheckpoint`` (in :mod:`ravi.fabric.checkpoint`) is
    the source-of-truth shape; ``CheckpointRef`` is the lightweight
    locator carried inside :class:`AgentActivationContract`. Use
    :meth:`from_run_checkpoint` to build a ref from a freshly persisted
    checkpoint.
    """

    agent_id_str: str
    checkpoint_id: str         # Globally unique (e.g. ulid or uuid hex)
    sequence: int              # Monotonically increasing per agent
    store_uri: str             # Storage backend URI (e.g. "redis://...", "s3://...")
    run_id: str = ""           # Pairs with agent_id_str as the store's primary key
    byte_size: int = 0
    created_at: str = ""       # ISO-8601

    @classmethod
    def from_run_checkpoint(
        cls,
        checkpoint: object,
        *,
        store_uri: str,
        byte_size: int = 0,
    ) -> CheckpointRef:
        """Build a ``CheckpointRef`` from a persisted ``RunCheckpoint``.

        Accepts the checkpoint as ``object`` to avoid a circular import
        on :mod:`ravi.fabric.checkpoint`; the real type contract
        is :class:`ravi.kernel.runtime.RunCheckpoint`.
        """
        return cls(
            agent_id_str=getattr(checkpoint, "agent_id"),
            checkpoint_id=getattr(checkpoint, "checkpoint_id"),
            sequence=getattr(checkpoint, "iteration", 0),
            store_uri=store_uri,
            run_id=getattr(checkpoint, "run_id", ""),
            byte_size=byte_size,
            created_at=str(getattr(checkpoint, "created_at", "") or ""),
        )


@dataclass(frozen=True, slots=True)
class AgentActivationContract:
    """
    The full contract governing a single activation of a logical agent.
    Carried in the Envelope to give the runtime everything it needs to
    activate, execute within budget, checkpoint, and hibernate.
    """

    lifecycle_state: AgentLifecycleState
    trigger: ActivationTrigger | None = None
    lease: ExecutionLease | None = None
    last_checkpoint: CheckpointRef | None = None
    # Recursion depth — number of activations on the current causal chain
    depth: int = 0
    # Hard ceiling on recursive activations for this agent family
    max_depth: int = 32


@runtime_checkable
class Checkpointable(Protocol):
    """Protocol for agent state that can be serialized to a checkpoint."""

    async def to_checkpoint(self) -> dict[str, Any]: ...

    @classmethod
    async def from_checkpoint(cls, data: dict[str, Any]) -> Checkpointable: ...


@runtime_checkable
class ActivationAware(Protocol):
    """Protocol for runtime components that understand activation lifecycle."""

    async def on_activating(self, contract: AgentActivationContract) -> None: ...
    async def on_hibernating(self, contract: AgentActivationContract) -> None: ...
    async def on_suspended(self, contract: AgentActivationContract) -> None: ...

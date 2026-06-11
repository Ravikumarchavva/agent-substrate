"""Agent protocol and Checkpoint — the runtime contract for all agent implementations.

Every agent registered with an ``AgentRuntime`` must satisfy the ``Agent``
protocol.  This contract enables distributed runtimes to:

- Route messages to agents by ``AgentId``
- Checkpoint and resume agents across restarts (``save_state``/``load_state``)
- Bind agents to their runtime at registration time (``bind``)

``Checkpoint`` is the serializable snapshot produced by ``save_state``.
Fabric's durable runner and supervision-v2 resumability depend on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from ravi.kernel.content import JsonObject
from ravi.kernel.identity import AgentId
from ravi.kernel.message import MessageContext, Payload, RuntimeRef


@runtime_checkable
class Agent(Protocol):
    """Contract every agent registered with an AgentRuntime must satisfy.

    ``id`` — the stable routing identity for this agent instance.

    ``bind`` — called once by the runtime after registration so the agent
    can store a reference to the runtime for sending/publishing.

    ``on_message`` — the single entry point for all incoming messages.
    Returns the reply payload (or ``None`` for fire-and-forget).

    ``save_state`` / ``load_state`` — produce/consume a JSON-serializable
    snapshot so the agent can be checkpointed and resumed across restarts,
    distributed moves (Ray actors, K8s pod rescheduling), or HITL pauses.
    Implementations that don't need persistence should return ``{}`` from
    ``save_state`` and ignore ``load_state``.
    """

    id: AgentId

    async def bind(self, runtime: RuntimeRef) -> None: ...

    async def on_message(
        self, ctx: MessageContext, payload: Payload
    ) -> Payload | None: ...

    async def save_state(self) -> JsonObject: ...

    async def load_state(self, state: JsonObject) -> None: ...


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A serializable snapshot of an agent's state at a specific point in time.

    ``run_id`` — the execution run this checkpoint belongs to.
    ``agent_id`` — the agent that produced the snapshot.
    ``seq`` — monotonically increasing sequence number within the run.
    ``state`` — opaque JSON dict returned by ``Agent.save_state()``.
    ``created_at`` — wall-clock time of the snapshot.

    Checkpoints are persisted by fabric's durable runner and consumed
    by supervision-v2 resumability to rebuild agent state after a crash
    or a deliberate pause.
    """

    run_id: str
    agent_id: AgentId
    seq: int
    state: JsonObject = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


__all__ = ["Agent", "Checkpoint"]

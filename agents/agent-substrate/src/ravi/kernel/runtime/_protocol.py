"""Runtime protocol types.

Defines the ``AgentRuntime`` protocol that every runtime backend must
implement.

All types use strict signatures — no ``Any`` in the public API.
Identity types are imported from ``_identity.py``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ravi.kernel.runtime._identity import AgentId, TopicId
from ravi.kernel.runtime._contracts import MessageHandler


# ---------------------------------------------------------------------------
# AgentRuntime protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentRuntime(Protocol):
    """Contract that all runtime backends implement.

    ``LocalRuntime`` (in-process, asyncio-only) ships with the framework.
    Production backends (gRPC, Restate, NATS) implement this same protocol
    in ``integrations/runtime/``.
    """

    async def send_message(
        self,
        message: object,
        *,
        sender: AgentId | None = None,
        recipient: AgentId,
    ) -> object:
        """Point-to-point: deliver *message* to *recipient* and return its response."""
        ...

    async def publish_message(
        self,
        message: object,
        *,
        sender: AgentId | None = None,
        topic: TopicId,
    ) -> None:
        """Pub/sub: broadcast *message* to all subscribers of *topic*."""
        ...

    async def register(
        self,
        agent_type: str,
        handler: MessageHandler,
    ) -> None:
        """Register an agent type with its message handler."""
        ...

    async def subscribe(
        self,
        agent_type: str,
        topic: TopicId,
    ) -> None:
        """Bind *agent_type* so that all instances receive messages on *topic*."""
        ...

    async def start(self) -> None:
        """Start the runtime (connect transports, spin up workers, etc.)."""
        ...

    async def stop(self) -> None:
        """Gracefully shut down the runtime."""
        ...

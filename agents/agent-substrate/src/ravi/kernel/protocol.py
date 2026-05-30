"""AgentRuntime protocol — the contract every runtime backend implements.

``LocalRuntime`` (in-process asyncio) ships with the framework.
Production backends (gRPC, NATS, Restate) implement this same protocol
in ``fabric/runtime/``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ravi.kernel.identity import AgentId, TopicId
from ravi.kernel.message import MessageHandler


@runtime_checkable
class AgentRuntime(Protocol):
    """Contract that all runtime backends must implement."""

    async def send_message(
        self,
        payload: object,
        *,
        sender: AgentId | None = None,
        recipient: AgentId,
    ) -> object:
        """Point-to-point: deliver *payload* to *recipient*, return its reply."""
        ...

    async def publish_message(
        self,
        payload: object,
        *,
        sender: AgentId | None = None,
        topic: TopicId,
    ) -> None:
        """Pub/sub: broadcast *payload* to all subscribers of *topic*."""
        ...

    async def register(self, agent_type: str, handler: MessageHandler) -> None:
        """Register a handler for *agent_type*.  Replaces any existing handler."""
        ...

    async def subscribe(self, agent_type: str, topic: TopicId) -> None:
        """Bind *agent_type* so its instances receive messages on *topic*."""
        ...

    async def unregister(self, agent_type: str) -> None:
        """Remove the handler for *agent_type*."""
        ...

    async def unsubscribe(self, agent_type: str, topic: TopicId) -> None:
        """Remove the *agent_type* → *topic* subscription."""
        ...

    async def start(self) -> None:
        """Start the runtime (connect transports, spin up workers, etc.)."""
        ...

    async def stop(self) -> None:
        """Gracefully shut down the runtime."""
        ...


__all__ = ["AgentRuntime"]

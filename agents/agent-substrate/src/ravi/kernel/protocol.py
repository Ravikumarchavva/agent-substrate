"""AgentRuntime protocol — the contract every runtime backend implements.

``LocalRuntime`` (in-process asyncio) ships with the framework.
Production backends (gRPC, NATS, Restate) implement this same protocol
in ``agents/runtime/``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ravi.kernel.identity import AgentId, TopicId
from ravi.kernel.message import MessageHandler


@runtime_checkable
class AgentRuntime(Protocol):
    """Contract that all runtime backends must implement.

    Handlers and subscriptions are keyed by full ``AgentId`` (type + key),
    not just by agent type string.  This allows multiple agents of the same
    type to coexist in one runtime — essential for orchestrator/subagent trees
    where several ``ReActAgent`` instances run in parallel.
    """

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

    async def register(self, agent_id: AgentId, handler: MessageHandler) -> None:
        """Register *handler* for the specific *agent_id* instance.

        Keyed by the full (type, key) pair — two agents of the same type
        but different keys are registered and dispatched independently.
        """
        ...

    async def subscribe(self, agent_id: AgentId, topic: TopicId) -> None:
        """Subscribe *agent_id* so it receives messages published on *topic*."""
        ...

    async def unregister(self, agent_id: AgentId) -> None:
        """Remove the handler for *agent_id*."""
        ...

    async def unsubscribe(self, agent_id: AgentId, topic: TopicId) -> None:
        """Remove the *agent_id* → *topic* subscription."""
        ...

    async def start(self) -> None:
        """Start the runtime (connect transports, spin up workers, etc.)."""
        ...

    async def stop(self) -> None:
        """Gracefully shut down the runtime."""
        ...


__all__ = ["AgentRuntime"]

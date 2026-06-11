"""AgentRuntime protocol — the contract every runtime backend implements.

``LocalRuntime`` (in-process asyncio) ships with the framework.
Production backends (gRPC, NATS, Restate) implement this same protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ravi.kernel.identity import AgentId, TopicId
from ravi.kernel.message import MessageHandler, Subscription

if TYPE_CHECKING:
    from ravi.kernel.agent import Agent


@runtime_checkable
class AgentRuntime(Protocol):
    """Contract that all runtime backends must implement.

    Agents are keyed by full ``AgentId`` (type + key + namespace), not just
    by agent type string.  This allows multiple agents of the same type to
    coexist — essential for orchestrator/subagent trees.
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

    async def register_agent(self, agent: "Agent") -> None:
        """Register an ``Agent`` instance.  The runtime calls ``agent.bind(self)``."""
        ...

    async def register(self, agent_id: AgentId, handler: MessageHandler) -> None:
        """Register a bare handler for *agent_id* (for lightweight use cases)."""
        ...

    async def subscribe(self, agent_id: AgentId, topic: TopicId) -> Subscription:
        """Subscribe *agent_id* so it receives messages published on *topic*.

        Returns a ``Subscription`` object that can be passed to ``unsubscribe``.
        """
        ...

    async def unregister(self, agent_id: AgentId) -> None:
        """Remove the handler for *agent_id*."""
        ...

    async def unsubscribe(self, subscription: Subscription) -> None:
        """Remove a subscription returned by ``subscribe``."""
        ...

    async def start(self) -> None:
        """Start the runtime (connect transports, spin up workers, etc.)."""
        ...

    async def stop(self) -> None:
        """Gracefully shut down the runtime."""
        ...


__all__ = ["AgentRuntime"]

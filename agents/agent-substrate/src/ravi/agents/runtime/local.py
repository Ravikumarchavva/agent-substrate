"""LocalRuntime — in-process asyncio agent runtime.

Implements the ``AgentRuntime`` Protocol from ``ravi.kernel`` using a plain
dict for handler registration and ``asyncio.gather`` for pub/sub fanout.
Suitable for single-process use (CLI, tests, notebook demos).
Production deployments swap this for a gRPC or NATS-backed runtime.

Handlers and subscriptions are keyed by full ``AgentId`` (type + key), not
just by agent type string.  This allows multiple agents of the same type to
coexist — e.g. three specialist ``ReActAgent`` instances in one orchestrator
tree are all addressable independently.

Usage::

    async with LocalRuntime() as rt:
        agent = ReActAgent("assistant", rt, model=llm)
        await rt.register(agent.id, agent.on_message)
        result = await rt.send_message("Hello", recipient=agent.id)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from ravi.kernel.errors import AgentNotFoundError
from ravi.kernel.identity import AgentId, TopicId
from ravi.kernel.message import MessageContext, MessageHandler, Subscription
from ravi.kernel.agent import Agent

logger = logging.getLogger(__name__)


class LocalRuntime:
    """In-process asyncio runtime — AgentId-keyed registry + asyncio pub/sub fanout."""

    def __init__(self) -> None:
        self._handlers: dict[AgentId, MessageHandler] = {}
        self._topic_subs: dict[str, list[MessageHandler]] = {}
        self._started = False

    # -- Async context manager -----------------------------------------------

    async def __aenter__(self) -> LocalRuntime:
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    # -- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._started = True
        logger.debug("LocalRuntime started")

    async def stop(self) -> None:
        self._started = False
        logger.debug("LocalRuntime stopped")

    # -- Handler registry ----------------------------------------------------

    async def register_agent(self, agent: Agent) -> None:
        """Register an ``Agent`` instance. The runtime calls ``agent.bind(self)``."""
        await agent.bind(self)
        await self.register(agent.id, agent.on_message)

    async def register(self, agent_id: AgentId, handler: MessageHandler) -> None:
        """Register *handler* for the specific *agent_id* instance."""
        self._handlers[agent_id] = handler

    async def unregister(self, agent_id: AgentId) -> None:
        """Remove the handler for *agent_id*."""
        self._handlers.pop(agent_id, None)

    # -- Pub/sub subscription -----------------------------------------------

    async def subscribe(self, agent_id: AgentId, topic: TopicId) -> Subscription:
        """Subscribe *agent_id* to *topic* — its handler receives published messages.

        Returns a ``Subscription`` object that can be passed to ``unsubscribe``.
        """
        handler = self._handlers.get(agent_id)
        if handler is not None:
            key = f"{topic.type}/{topic.source}"
            self._topic_subs.setdefault(key, []).append(handler)
        return Subscription(topic=topic, agent_id=agent_id)

    async def unsubscribe(self, subscription: Subscription) -> None:
        """Remove a subscription returned by ``subscribe``."""
        agent_id = subscription.agent_id
        topic = subscription.topic
        handler = self._handlers.get(agent_id)
        if handler is None:
            return
        key = f"{topic.type}/{topic.source}"
        subs = self._topic_subs.get(key, [])
        try:
            subs.remove(handler)
        except ValueError:
            pass

    # -- Message delivery ----------------------------------------------------

    async def send_message(
        self,
        payload: object,
        *,
        sender: AgentId | None = None,
        recipient: AgentId,
    ) -> object:
        """Point-to-point delivery to a specific agent instance."""
        handler = self._handlers.get(recipient)
        if handler is None:
            raise AgentNotFoundError(
                f"No handler registered for {recipient} — "
                "call await runtime.register(agent.id, agent.on_message) first."
            )
        ctx = MessageContext(
            runtime=self,  # type: ignore[arg-type]
            sender=sender,
            correlation_id=uuid.uuid4().hex,
            agent_id=recipient,
        )
        return await handler(ctx, payload)

    async def publish_message(
        self,
        payload: object,
        *,
        sender: AgentId | None = None,
        topic: TopicId,
    ) -> None:
        """Pub/sub broadcast — all subscribers of *topic* receive *payload*."""
        key = f"{topic.type}/{topic.source}"
        handlers = self._topic_subs.get(key, [])
        if not handlers:
            return
        ctx = MessageContext(
            runtime=self,  # type: ignore[arg-type]
            sender=sender,
            correlation_id=uuid.uuid4().hex,
            agent_id=AgentId(type=topic.type, key=topic.source),
        )
        await asyncio.gather(
            *(h(ctx, payload) for h in handlers),
            return_exceptions=True,
        )

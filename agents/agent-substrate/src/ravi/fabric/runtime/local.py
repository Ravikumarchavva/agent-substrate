"""LocalRuntime — in-process asyncio agent runtime.

Implements the ``AgentRuntime`` Protocol from ``ravi.kernel`` using a plain
dict for handler registration and ``asyncio.gather`` for pub/sub fanout.
Suitable for single-process use (CLI, tests, notebook demos).
Production deployments swap this for a gRPC or NATS-backed runtime.

Usage::

    async with LocalRuntime() as rt:
        agent = AssistantAgent("assistant", rt, model=llm)
        await rt.register(agent.id.type, agent.on_message)
        result = await rt.send_message("Hello", recipient=agent.id)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from ravi.kernel.identity import AgentId, TopicId
from ravi.kernel.message import MessageContext, MessageHandler

logger = logging.getLogger(__name__)


class LocalRuntime:
    """In-process asyncio runtime — dict registry + asyncio pub/sub fanout."""

    def __init__(self) -> None:
        self._handlers: dict[str, MessageHandler] = {}
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

    async def register(self, agent_type: str, handler: MessageHandler) -> None:
        self._handlers[agent_type] = handler

    async def unregister(self, agent_type: str) -> None:
        self._handlers.pop(agent_type, None)

    # -- Pub/sub subscription -----------------------------------------------

    async def subscribe(self, agent_type: str, topic: TopicId) -> None:
        handler = self._handlers.get(agent_type)
        if handler is not None:
            self._topic_subs.setdefault(topic.type, []).append(handler)

    async def unsubscribe(self, agent_type: str, topic: TopicId) -> None:
        # Best-effort; exact handler match not tracked here.
        pass

    # -- Message delivery ----------------------------------------------------

    async def send_message(
        self,
        payload: object,
        *,
        sender: AgentId | None = None,
        recipient: AgentId,
    ) -> object:
        handler = self._handlers.get(recipient.type)
        if handler is None:
            raise LookupError(
                f"No handler registered for agent type '{recipient.type}'"
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
        handlers = self._topic_subs.get(topic.type, [])
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

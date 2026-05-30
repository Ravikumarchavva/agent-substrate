"""BaseRemoteRuntime — shared ABC for remote-capable runtime backends.

``GrpcRuntime`` and ``RestateRuntime`` both need local handler dispatch
(registry look-up + ``MessageContext`` construction) plus topic bindings.
Rather than duplicating this logic, they inherit from this ABC and only
implement the transport-specific abstract methods.

Inherits common handler registry from ``BaseRuntime`` (in ``core/runtime``)
and adds:
- Lifecycle guards (no register after start, no send before start)
- Local dispatch helpers (``_dispatch_local``, ``_dispatch_local_subscriber``)
- Abstract ``_remote_send`` for transport-specific point-to-point delivery
"""

from __future__ import annotations
from ravi.logger import setup_logging

from abc import abstractmethod

from ravi.kernel.runtime import (
    AgentId,
    Envelope,
    MessageContext,
    MessageHandler,
    TopicId
)
from ravi.agents.runtime.base import BaseRuntime

logger = setup_logging()


class BaseRemoteRuntime(BaseRuntime):
    """Abstract base for remote-capable ``AgentRuntime`` backends.

    Extends :class:`BaseRuntime` with:

    - Lifecycle guards (block registration after ``start()``, block send
      before ``start()``)
    - Local dispatch path for locally-registered handlers
    - Abstract :meth:`_remote_send` for transport-specific delivery

    Subclasses implement:
    - :meth:`_remote_send` — transport-specific point-to-point delivery
    - :meth:`start` / :meth:`stop` — transport lifecycle
    """

    def __init__(self) -> None:
        super().__init__()

    # -- AgentRuntime protocol (concrete) -----------------------------------

    async def register(
        self,
        agent_type: str,
        handler: MessageHandler,
    ) -> None:
        """Register a local agent type.

        Raises ``RuntimeError`` if called after ``start()``.
        Raises ``ValueError`` if *agent_type* is empty or *handler* not callable.
        """
        # H7 fix: block registration after start
        if self._started:
            raise RuntimeError(f"{type(self).__name__}: cannot register after start()")
        # M3 fix: input validation
        if not agent_type or not isinstance(agent_type, str):
            raise ValueError("agent_type must be a non-empty string")
        if not callable(handler):
            raise ValueError("handler must be callable")
        await super().register(agent_type, handler)
        logger.debug("%s: registered agent type %r", type(self).__name__, agent_type)

    async def subscribe(
        self,
        agent_type: str,
        topic: TopicId,
    ) -> None:
        """Bind *agent_type* so instances receive messages on *topic*."""
        await super().subscribe(agent_type, topic)

    async def send_message(
        self,
        message: object,
        *,
        sender: AgentId | None = None,
        recipient: AgentId,
    ) -> object:
        """Dispatch: local handler if registered, otherwise :meth:`_remote_send`.

        Raises ``RuntimeError`` if called before ``start()``.
        """
        # H7 fix: lifecycle guard
        if not self._started:
            raise RuntimeError(f"{type(self).__name__}: cannot send before start()")
        if recipient.type in self._handlers:
            return await self._dispatch_local(message, sender=sender, target=recipient)
        return await self._remote_send(message, sender=sender, recipient=recipient)

    async def publish_message(
        self,
        message: object,
        *,
        sender: AgentId | None = None,
        topic: TopicId,
    ) -> None:
        """Broadcast to all locally-subscribed handlers for *topic*.

        H6 fix: one handler failure doesn't stop delivery to others.
        """
        # H7 fix: lifecycle guard
        if not self._started:
            raise RuntimeError(f"{type(self).__name__}: cannot publish before start()")
        for agent_type, bound_topic in self._topic_bindings:
            if bound_topic == topic and agent_type in self._handlers:
                # H6 fix: isolate each subscriber
                try:
                    await self._dispatch_local_subscriber(
                        message, sender=sender, topic=topic, agent_type=agent_type
                    )
                except Exception:
                    logger.exception(
                        "%s: fan-out to %r failed for topic %s, continuing",
                        type(self).__name__,
                        agent_type,
                        topic,
                    )

    # -- Internal helpers ---------------------------------------------------

    def _make_correlation_id(
        self, message: object, *, sender: AgentId | None, target: AgentId | TopicId
    ) -> str:
        from ravi.kernel.content import TextBlock

        # Envelope only needs content for routing; we just need the auto-generated correlation_id
        content = (
            message if isinstance(message, list) else [TextBlock(text=str(message))]
        )
        return Envelope(sender=sender, target=target, content=content).correlation_id

    async def _dispatch_local(
        self,
        message: object,
        *,
        sender: AgentId | None,
        target: AgentId,
    ) -> object:
        """Invoke the local handler for *target* and return its response."""
        handler = self._handlers[target.type]
        ctx = MessageContext(
            runtime=self,
            sender=sender,
            correlation_id=self._make_correlation_id(
                message, sender=sender, target=target
            ),
            agent_id=target,
        )
        return await handler(ctx, message)

    async def _dispatch_local_subscriber(
        self,
        message: object,
        *,
        sender: AgentId | None,
        topic: TopicId,
        agent_type: str,
    ) -> None:
        """Invoke a local subscriber handler and discard its return value."""
        handler = self._handlers[agent_type]
        ctx = MessageContext(
            runtime=self,
            sender=sender,
            correlation_id=self._make_correlation_id(
                message, sender=sender, target=topic
            ),
            agent_id=AgentId(type=agent_type, key=topic.source),
        )
        await handler(ctx, message)

    # -- Transport interface (subclass implements) ---------------------------

    @abstractmethod
    async def _remote_send(
        self,
        message: object,
        *,
        sender: AgentId | None,
        recipient: AgentId,
    ) -> object:
        """Send *message* to a remote agent via the transport layer.

        Called by :meth:`send_message` when no local handler matches
        ``recipient.type``.
        """

    @abstractmethod
    async def start(self) -> None:
        """Start the transport layer (bind server, connect client, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the transport layer gracefully."""

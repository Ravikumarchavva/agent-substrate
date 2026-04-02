"""Local in-process runtime — the default ``AgentRuntime`` implementation.

Uses ``asyncio`` primitives only — no external infrastructure required.
Agents are lazily instantiated from their factory on first message.  The
runtime composes a ``Dispatcher`` (routing) and ``Supervisor`` (crash
recovery) internally.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Set

from raavan.core.runtime._base import BaseRuntime
from raavan.core.runtime._protocol import AgentFactory, AgentId, TopicId
from raavan.core.runtime._types import (
    CancellationToken,
    Envelope,
    MessageContext,
    MessageHandler,
    RestartPolicy,
)
from raavan.core.runtime._mailbox import Mailbox
from raavan.core.runtime._dispatcher import AgentNotFoundError, Dispatcher
from raavan.core.runtime._supervisor import Supervisor

logger = logging.getLogger("raavan.core.runtime.local")

# Default mailbox capacity per agent
_DEFAULT_CAPACITY = 100
_DEFAULT_SEND_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class HandlerError(Exception):
    """Raised when a message handler crashes.

    Wraps the original exception so callers of ``send_message`` receive
    a proper error instead of a silent ``None``.
    """


class LocalRuntime(BaseRuntime):
    """In-process ``AgentRuntime`` backed by ``asyncio.Queue`` mailboxes.

    This is the "batteries-included" runtime that works out of the box
    with zero infrastructure.  Production deployments can swap in a
    ``GrpcRuntime`` or ``RestateRuntime`` that inherits from the same
    ``BaseRuntime`` ABC.

    Parameters
    ----------
    restart_policy:
        Supervisor restart policy applied to all agents.
    mailbox_capacity:
        Default mailbox size for each agent instance.
    send_timeout:
        Maximum seconds ``send_message`` waits for a response.
        ``None`` disables the timeout. Default: 30 seconds.
    """

    __slots__ = (
        "_dispatcher",
        "_supervisor",
        "_agents_started",
        "_pending_responses",
        "_mailbox_capacity",
        "_send_timeout",
        "_active_handlers",
    )

    def __init__(
        self,
        restart_policy: RestartPolicy | None = None,
        mailbox_capacity: int = _DEFAULT_CAPACITY,
        send_timeout: float | None = _DEFAULT_SEND_TIMEOUT,
    ) -> None:
        super().__init__()
        self._dispatcher = Dispatcher()
        self._supervisor = Supervisor(restart_policy)
        self._agents_started: Set[AgentId] = set()
        self._pending_responses: Dict[str, asyncio.Future[Any]] = {}
        self._mailbox_capacity = mailbox_capacity
        self._send_timeout = send_timeout
        self._active_handlers = 0

    # -- AgentRuntime protocol ----------------------------------------------

    async def send_message(
        self,
        message: Any,
        *,
        sender: AgentId | None = None,
        recipient: AgentId,
        cancellation_token: CancellationToken | None = None,
    ) -> Any:
        """Point-to-point message delivery with response.

        Lazily creates the recipient agent if it hasn't been instantiated yet.
        Returns the value produced by the recipient's handler.

        Raises ``HandlerError`` if the handler crashes.
        Raises ``TimeoutError`` if no response within ``send_timeout``.
        Raises ``asyncio.CancelledError`` if *cancellation_token* fires.
        """
        if cancellation_token is not None and cancellation_token.cancelled:
            raise asyncio.CancelledError("CancellationToken already cancelled")

        await self._ensure_agent(recipient)

        envelope = Envelope(sender=sender, target=recipient, payload=message)

        # Create a Future so we can collect the handler's return value
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending_responses[envelope.correlation_id] = future

        # Link cancellation token to the future
        if cancellation_token is not None:
            cancellation_token.link_future(future)

        # C3 fix: if dispatch fails, clean up the future before re-raising
        try:
            await self._dispatcher.dispatch(envelope)
        except Exception:
            self._pending_responses.pop(envelope.correlation_id, None)
            if not future.done():
                future.cancel()
            raise

        # C5 fix: await with configurable timeout
        try:
            if self._send_timeout is not None:
                result = await asyncio.wait_for(future, timeout=self._send_timeout)
            else:
                result = await future
        except asyncio.TimeoutError:
            self._pending_responses.pop(envelope.correlation_id, None)
            if not future.done():
                future.cancel()
            raise TimeoutError(
                f"send_message to {recipient} timed out after {self._send_timeout}s"
            ) from None

        return result

    async def publish_message(
        self,
        message: Any,
        *,
        sender: AgentId | None = None,
        topic: TopicId,
    ) -> None:
        """Fire-and-forget broadcast to all topic subscribers.

        Lazily creates subscriber agent instances if needed.
        """
        # Ensure all subscribed agents are running
        for agent_type, bound_topic in self._topic_bindings:
            if bound_topic == topic:
                # Create a default-keyed instance for each type
                aid = AgentId(type=agent_type, key=topic.source)
                await self._ensure_agent(aid)

        envelope = Envelope(sender=sender, target=topic, payload=message)
        await self._dispatcher.dispatch(envelope)

    async def register(
        self,
        agent_type: str,
        factory: AgentFactory,
    ) -> None:
        """Register an agent type and its factory."""
        await super().register(agent_type, factory)

    async def subscribe(
        self,
        agent_type: str,
        topic: TopicId,
    ) -> None:
        """Bind *agent_type* to a topic so instances receive its messages."""
        await super().subscribe(agent_type, topic)
        self._dispatcher.subscribe_to_topic(topic, agent_type)
        logger.debug("subscribed %r to %s", agent_type, topic)

    async def start(self) -> None:
        """No-op for LocalRuntime — agents are lazy-created on first message."""
        self._started = True
        logger.info("LocalRuntime started")

    async def stop(self) -> None:
        """Gracefully shut down: cancel agent loops, drain mailboxes."""
        self._started = False
        await self._supervisor.stop_all()

        # Close all mailboxes
        for aid in self._dispatcher.registered_agents:
            mbox = self._dispatcher.get_mailbox(aid)
            if mbox is not None:
                mbox.close()

        # Cancel any pending response futures
        for cid, future in self._pending_responses.items():
            if not future.done():
                future.cancel()
        self._pending_responses.clear()
        self._agents_started.clear()

        logger.info("LocalRuntime stopped")

    async def stop_when_idle(self, poll_interval: float = 0.05) -> None:
        """Wait until all mailboxes are empty and no pending responses, then stop.

        Useful in notebooks and scripts where you want to process
        all published messages before shutting down.
        """
        while True:
            # Check if any mailbox still has messages
            has_work = False
            for aid in self._dispatcher.registered_agents:
                mbox = self._dispatcher.get_mailbox(aid)
                if mbox is not None and not mbox.is_empty:
                    has_work = True
                    break
            if (
                not has_work
                and not self._pending_responses
                and self._active_handlers == 0
            ):
                break
            await asyncio.sleep(poll_interval)

        await self.stop()

    # -- lazy agent lifecycle -----------------------------------------------

    async def _ensure_agent(self, agent_id: AgentId) -> None:
        """Create and start the agent if it doesn't exist yet."""
        if agent_id in self._agents_started:
            return

        if agent_id.type not in self._factories:
            raise AgentNotFoundError(
                f"no factory registered for agent type {agent_id.type!r}"
            )

        # Create mailbox and register with dispatcher
        mailbox = Mailbox(capacity=self._mailbox_capacity)
        self._dispatcher.register_agent(agent_id, mailbox)
        self._agents_started.add(agent_id)

        # Start supervised message loop
        handler = self._handlers[agent_id.type]
        self._supervisor.supervise(
            agent_id,
            lambda aid=agent_id, mb=mailbox, h=handler: self._agent_loop(aid, mb, h),
        )

        logger.debug("lazily created agent %s", agent_id)

    async def _agent_loop(
        self,
        agent_id: AgentId,
        mailbox: Mailbox,
        handler: MessageHandler,
    ) -> None:
        """Process messages from the mailbox until closed."""
        while True:
            try:
                envelope = await mailbox.get()
            except StopAsyncIteration:
                break  # mailbox closed

            ctx = MessageContext(
                runtime=self,
                sender=envelope.sender,
                correlation_id=envelope.correlation_id,
                agent_id=agent_id,
            )

            result: Any = None
            error: Exception | None = None
            self._active_handlers += 1
            try:
                result = await handler(ctx, envelope.payload)
            except Exception as exc:
                logger.exception(
                    "handler for %s raised on message %s",
                    agent_id,
                    envelope.correlation_id,
                )
                error = exc
            finally:
                self._active_handlers -= 1
                # C4 fix: always resolve the future, even on CancelledError
                future = self._pending_responses.pop(envelope.correlation_id, None)
                if future is not None and not future.done():
                    if error is not None:
                        # H4 fix: propagate handler errors to callers
                        future.set_exception(
                            HandlerError(f"handler for {agent_id} raised: {error}")
                        )
                    else:
                        future.set_result(result)

    # -- introspection ------------------------------------------------------

    @property
    def active_agents(self) -> list[AgentId]:
        return list(self._agents_started)

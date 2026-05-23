"""Local in-process runtime — the default ``AgentRuntime`` implementation.

Uses ``asyncio`` primitives only — no external infrastructure required.
Agent mailboxes and loops are created lazily on first message. The runtime
composes:
- ``Dispatcher``          — message routing + fan-out
- ``Supervisor``          — Erlang-style crash recovery
- ``ResourceLockManager`` — advisory file/resource locking
- ``ClientWriteChannel``  — sequenced multi-agent client output
- ``SagaCoordinator``     — exactly-once critical action execution

All messages are ``Envelope`` objects carrying ``list[ContentBlock]``
as their multimodal content.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ravi.core.messages.content import ContentBlock, TextBlock
from ravi.core.runtime._base import BaseRuntime
from ravi.core.runtime._identity import AgentId, TopicId
from ravi.core.runtime._contracts import (
    CancellationToken,
    Envelope,
    MessageContext,
    MessageHandler,
    RestartPolicy,
)
from ravi.core.runtime._mailbox import Mailbox
from ravi.core.runtime._dispatcher import Dispatcher
from ravi.core.runtime._errors import (
    AgentNotFoundError,
    EnvelopeExpiredError,
    HandlerError,
)
from ravi.core.runtime._supervisor import Supervisor
from ravi.core.runtime._resource_lock import ResourceLockManager
from ravi.core.runtime._client_channel import ClientWriteChannel, ClientSink
from ravi.core.runtime._saga import SagaCoordinator, SagaStore

logger = logging.getLogger("ravi.core.runtime.local")

# Re-export so existing ``from _local import HandlerError`` works.
__all__ = ["LocalRuntime", "HandlerError"]

# Default mailbox capacity per agent
_DEFAULT_CAPACITY = 100
_DEFAULT_SEND_TIMEOUT = 30.0


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
    resource_lock_timeout:
        Default timeout for resource lock acquisition.
    client_sink:
        Optional async sink for client-bound frames.  When provided,
        a ``ClientWriteChannel`` is created automatically.
    saga_store:
        Optional persistent store for saga records.
    """

    __slots__ = (
        "_dispatcher",
        "_supervisor",
        "_agents_started",
        "_pending_responses",
        "_mailbox_capacity",
        "_send_timeout",
        "_active_handlers",
        "_resource_locks",
        "_client_channel",
        "_saga_coordinator",
    )

    def __init__(
        self,
        restart_policy: RestartPolicy | None = None,
        mailbox_capacity: int = _DEFAULT_CAPACITY,
        send_timeout: float | None = _DEFAULT_SEND_TIMEOUT,
        resource_lock_timeout: float | None = 30.0,
        client_sink: Optional[ClientSink] = None,
        saga_store: Optional[SagaStore] = None,
    ) -> None:
        super().__init__()
        self._dispatcher = Dispatcher()
        self._supervisor = Supervisor(restart_policy)
        self._agents_started: set[AgentId] = set()
        self._pending_responses: dict[str, asyncio.Future[object]] = {}
        self._mailbox_capacity = mailbox_capacity
        self._send_timeout = send_timeout
        self._active_handlers = 0

        # New runtime subsystems
        self._resource_locks = ResourceLockManager(
            default_timeout=resource_lock_timeout
        )
        self._client_channel: Optional[ClientWriteChannel] = None
        if client_sink is not None:
            self._client_channel = ClientWriteChannel(sink=client_sink)
        self._saga_coordinator = SagaCoordinator(store=saga_store)

    # -- Subsystem accessors ------------------------------------------------

    @property
    def resource_locks(self) -> ResourceLockManager:
        """Access the resource lock manager for file/resource locking."""
        return self._resource_locks

    @property
    def client_channel(self) -> Optional[ClientWriteChannel]:
        """Access the client write channel (None if no sink configured)."""
        return self._client_channel

    @property
    def saga_coordinator(self) -> SagaCoordinator:
        """Access the saga coordinator for critical action management."""
        return self._saga_coordinator

    # -- AgentRuntime protocol ----------------------------------------------

    async def send_message(
        self,
        message: object,
        *,
        sender: AgentId | None = None,
        recipient: AgentId,
        cancellation_token: CancellationToken | None = None,
    ) -> object:
        """Point-to-point message delivery with response.

        ``message`` can be:
        - A ``list[ContentBlock]`` (preferred multimodal path)
        - A bare string (auto-wrapped in ``[TextBlock(text=...)]``)
        - Any other object (auto-wrapped in ``[TextBlock(text=str(...))]``)

        Lazily creates the recipient agent if it hasn't been instantiated yet.
        Returns the value produced by the recipient's handler.

        Raises ``HandlerError`` if the handler crashes.
        Raises ``TimeoutError`` if no response within ``send_timeout``.
        Raises ``asyncio.CancelledError`` if *cancellation_token* fires.
        """
        if cancellation_token is not None and cancellation_token.cancelled:
            raise asyncio.CancelledError("CancellationToken already cancelled")

        await self._ensure_agent(recipient)

        content = self._normalize_content(message)
        envelope = Envelope(sender=sender, target=recipient, content=content)

        # Create a Future so we can collect the handler's return value
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._pending_responses[envelope.correlation_id] = future

        # Link cancellation token to the future
        if cancellation_token is not None:
            cancellation_token.link_future(future)

        # If dispatch fails, clean up the future before re-raising
        try:
            await self._dispatcher.dispatch(envelope)
        except Exception:
            self._pending_responses.pop(envelope.correlation_id, None)
            if not future.done():
                future.cancel()
            raise

        # Await with configurable timeout
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
        message: object,
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
                aid = AgentId(type=agent_type, key=topic.source)
                await self._ensure_agent(aid)

        content = self._normalize_content(message)
        envelope = Envelope(sender=sender, target=topic, content=content)
        await self._dispatcher.dispatch(envelope)

    async def register(
        self,
        agent_type: str,
        handler: MessageHandler,
    ) -> None:
        """Register an agent type and its message handler."""
        await super().register(agent_type, handler)

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
        """Start the runtime and all subsystems."""
        self._started = True
        if self._client_channel is not None:
            await self._client_channel.start()
        logger.info("LocalRuntime started")

    async def stop(self) -> None:
        """Gracefully shut down: cancel agent loops, drain mailboxes, stop subsystems."""
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

        # Stop client channel
        if self._client_channel is not None:
            await self._client_channel.stop()

        logger.info("LocalRuntime stopped")

    async def stop_when_idle(self, poll_interval: float = 0.05) -> None:
        """Wait until all mailboxes are empty and no pending responses, then stop.

        Useful in notebooks and scripts where you want to process
        all published messages before shutting down.
        """
        while True:
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

        if agent_id.type not in self._handlers:
            raise AgentNotFoundError(
                f"no handler registered for agent type {agent_id.type!r}"
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

            # Skip expired envelopes
            if envelope.is_expired:
                logger.warning(
                    "dropping expired envelope %s (ttl exceeded)",
                    envelope.correlation_id,
                )
                future = self._pending_responses.pop(envelope.correlation_id, None)
                if future is not None and not future.done():
                    future.set_exception(
                        EnvelopeExpiredError(
                            f"envelope {envelope.correlation_id} expired before delivery"
                        )
                    )
                continue

            ctx = MessageContext(
                runtime=self,
                sender=envelope.sender,
                correlation_id=envelope.correlation_id,
                agent_id=agent_id,
            )

            result: object = None
            error: Exception | None = None
            self._active_handlers += 1
            try:
                result = await handler(ctx, envelope.content)
            except Exception as exc:
                logger.exception(
                    "handler for %s raised on message %s",
                    agent_id,
                    envelope.correlation_id,
                )
                error = exc
            finally:
                self._active_handlers -= 1
                future = self._pending_responses.pop(envelope.correlation_id, None)
                if future is not None and not future.done():
                    if error is not None:
                        future.set_exception(
                            HandlerError(f"handler for {agent_id} raised: {error}")
                        )
                    else:
                        future.set_result(result)

    # -- content normalization ----------------------------------------------

    @staticmethod
    def _normalize_content(message: object) -> list[ContentBlock]:
        """Normalize any message input to list[ContentBlock].

        Accepts:
        - list[ContentBlock] → pass through
        - str → wrap in [TextBlock]
        - Any other object → wrap in [TextBlock(text=str(...))]
        """
        if isinstance(message, list):
            return message  # type: ignore[return-value]
        if isinstance(message, str):
            return [TextBlock(text=message)]
        return [TextBlock(text=str(message))]

    # -- introspection ------------------------------------------------------

    @property
    def active_agents(self) -> list[AgentId]:
        return list(self._agents_started)

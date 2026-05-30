"""Local in-process runtime — the default ``AgentRuntime`` implementation.

Uses ``asyncio`` primitives only — no external infrastructure required.
Agent mailboxes and loops are created lazily on first message. The runtime
composes:

- :class:`Dispatcher`          — message routing + fan-out
- :class:`Supervisor`          — Erlang-style crash recovery
- :class:`ResourceLockManager` — advisory file/resource locking
- :class:`ClientWriteChannel`  — sequenced multi-agent client output
- :class:`SagaCoordinator`     — exactly-once critical action execution
- :class:`LeaseRegistry`       — exclusive-activation coordination (optional)

All messages are :class:`Envelope` objects carrying ``list[ContentBlock]``
as their multimodal content.

Distributed coordination
~~~~~~~~~~~~~~~~~~~~~~~~
Pass a :class:`LeaseRegistry` to coordinate exclusive activation across
workers. :meth:`_ensure_agent` acquires the lease before spawning the
agent loop; :meth:`hibernate` releases it. Without a registry the runtime
operates in single-worker mode and trusts the caller's routing.

Lifecycle state machine
~~~~~~~~~~~~~~~~~~~~~~~
Every active agent has an :class:`AgentActivationContract` tracking its
:class:`AgentLifecycleState`: DORMANT → ACTIVATING → ACTIVE →
HIBERNATING → DORMANT. Crash transitions to SUSPENDED before the
supervisor decides whether to restart.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ravi.kernel.messages.content import (
    CONTENT_BLOCK_TYPES,
    ContentBlock,
    TextBlock,
)
from ravi.kernel.runtime._backpressure import BackpressurePolicy
from ravi.fabric.runtime.base import BaseRuntime
from ravi.fabric.channel import ClientSink, ClientWriteChannel
from ravi.kernel.runtime._contracts import (
    CancellationToken,
    Envelope,
    MessageContext,
    MessageHandler,
    RestartPolicy,
)
from ravi.fabric.runtime.dispatcher import Dispatcher
from ravi.kernel.runtime._errors import (
    AgentNotFoundError,
    EnvelopeExpiredError,
    HandlerError,
    LeaseAcquisitionFailed,
)
from ravi.kernel.runtime._identity import AgentId, TopicId
from ravi.kernel.runtime._lease import (
    DEFAULT_LEASE_TTL_SECONDS,
    LeaseRegistry,
)
from ravi.kernel.runtime._lifecycle import (
    ActivationTrigger,
    AgentActivationContract,
    AgentLifecycleState,
    ExecutionLease,
)
from ravi.fabric.runtime.mailbox import Mailbox
from ravi.kernel.runtime._middleware import RoutingMiddleware
from ravi.fabric.locks import ResourceLockManager
from ravi.fabric.saga import SagaCoordinator, SagaStore
from ravi.fabric.runtime.supervisor import Supervisor

logger = logging.getLogger(__name__)

# Re-export so existing ``from _local import HandlerError`` works.
__all__ = ["LocalRuntime", "HandlerError"]

# Default mailbox capacity per agent
_DEFAULT_CAPACITY = 100
_DEFAULT_SEND_TIMEOUT = 30.0


class LocalRuntime(BaseRuntime):
    """In-process ``AgentRuntime`` backed by ``asyncio.Queue`` mailboxes.

    This is the "batteries-included" runtime that works out of the box
    with zero infrastructure. Production deployments swap in a
    ``DistributedRuntime`` or ``GrpcRuntime`` that inherits from the same
    :class:`BaseRuntime` ABC.

    Parameters
    ----------
    restart_policy:
        Supervisor restart policy applied to all agents.
    mailbox_capacity:
        Default mailbox size for each agent instance.
    mailbox_policy:
        :class:`BackpressurePolicy` applied to every agent's mailbox under
        non-blocking fan-out. Default: ``SHED``.
    send_timeout:
        Maximum seconds ``send_message`` waits for a response.
        ``None`` disables the timeout. Default: 30 seconds.
    resource_lock_timeout:
        Default timeout for resource lock acquisition.
    client_sink:
        Optional async sink for client-bound frames. When provided,
        a :class:`ClientWriteChannel` is created automatically.
    saga_store:
        Optional persistent store for saga records.
    lease_registry:
        Optional :class:`LeaseRegistry` for cross-worker exclusive
        activation. ``None`` means single-worker mode.
    lease_ttl_seconds:
        TTL applied to every acquired lease. Default 60s.
    worker_id:
        Stable identifier for this worker, propagated to every lease.
    """

    __slots__ = (
        "_dispatcher",
        "_supervisor",
        "_agents_started",
        "_pending_responses",
        "_mailbox_capacity",
        "_mailbox_policy",
        "_send_timeout",
        "_active_handlers",
        "_resource_locks",
        "_client_channel",
        "_saga_coordinator",
        "_activations",
        "_lease_ttl_seconds",
    )

    def __init__(
        self,
        restart_policy: RestartPolicy | None = None,
        mailbox_capacity: int = _DEFAULT_CAPACITY,
        mailbox_policy: BackpressurePolicy = BackpressurePolicy.SHED,
        send_timeout: float | None = _DEFAULT_SEND_TIMEOUT,
        resource_lock_timeout: float | None = 30.0,
        client_sink: Optional[ClientSink] = None,
        saga_store: Optional[SagaStore] = None,
        lease_registry: Optional[LeaseRegistry] = None,
        lease_ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
        worker_id: Optional[str] = None,
        routing_middleware: Optional[list[RoutingMiddleware]] = None,
    ) -> None:
        super().__init__(
            lease_registry=lease_registry,
            worker_id=worker_id,
            routing_middleware=routing_middleware,
        )
        self._dispatcher = Dispatcher()
        self._supervisor = Supervisor(restart_policy)
        self._agents_started: set[AgentId] = set()
        self._pending_responses: dict[str, asyncio.Future[object]] = {}
        self._mailbox_capacity = mailbox_capacity
        self._mailbox_policy = mailbox_policy
        self._send_timeout = send_timeout
        self._active_handlers = 0
        self._activations: dict[AgentId, AgentActivationContract] = {}
        self._lease_ttl_seconds = lease_ttl_seconds

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

    @property
    def dispatcher(self) -> Dispatcher:
        """Access the underlying dispatcher (for observer registration etc.)."""
        return self._dispatcher

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
        If the runtime has not been explicitly started, ``start()`` is called
        automatically — this keeps lifecycle consistent with remote runtime
        backends, where lifecycle is required.

        Raises ``HandlerError`` if the handler crashes.
        Raises ``TimeoutError`` if no response within ``send_timeout``.
        Raises ``LeaseAcquisitionFailed`` if a configured lease registry
        refuses to grant ownership of the recipient.
        Raises ``asyncio.CancelledError`` if *cancellation_token* fires.
        """
        if cancellation_token is not None and cancellation_token.cancelled:
            raise asyncio.CancelledError("CancellationToken already cancelled")

        await self._ensure_started()
        await self._ensure_agent(
            recipient,
            trigger=ActivationTrigger(
                trigger_type="message",
                source_id=sender and str(sender) or "<external>",
            ),
        )

        content = self._normalize_content(message)
        envelope = Envelope(sender=sender, target=recipient, content=content)

        # Pre-dispatch middleware: identity, tenant-isolation, depth, trust...
        allowed = await self._apply_routing_middleware(envelope)
        if not allowed:
            return None

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

        Lazily creates subscriber agent instances if needed and auto-starts
        the runtime if it has not been started yet — keeping lifecycle
        consistent with remote runtime backends.
        """
        await self._ensure_started()

        # Ensure all subscribed agents are running
        for agent_type, bound_topic in self._topic_bindings:
            if bound_topic == topic:
                aid = AgentId(type=agent_type, key=topic.source)
                await self._ensure_agent(
                    aid,
                    trigger=ActivationTrigger(
                        trigger_type="publish",
                        source_id=str(topic),
                    ),
                )

        content = self._normalize_content(message)
        envelope = Envelope(sender=sender, target=topic, content=content)

        # Pre-dispatch middleware applies to broadcasts as well.
        allowed = await self._apply_routing_middleware(envelope)
        if not allowed:
            return

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
        """Start the runtime and all subsystems.

        Idempotent: repeated calls have no effect. Called implicitly on the
        first ``send_message``/``publish_message`` to keep lifecycle on par
        with remote runtimes that *require* an explicit start.
        """
        if self._started:
            return
        self._started = True
        if self._client_channel is not None:
            await self._client_channel.start()
        logger.info("LocalRuntime %s started", self._worker_id)

    async def _ensure_started(self) -> None:
        """Auto-start the runtime if it has not been started yet."""
        if not self._started:
            await self.start()

    async def stop(self) -> None:
        """Gracefully shut down: cancel agent loops, drain mailboxes, stop subsystems."""
        self._started = False
        # Release every held lease before stopping the supervisor — that way
        # other workers can pick up the agents we own.
        await self._release_all_leases()
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
        self._activations.clear()

        # Stop client channel
        if self._client_channel is not None:
            await self._client_channel.stop()

        logger.info("LocalRuntime %s stopped", self._worker_id)

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

    async def _ensure_agent(
        self,
        agent_id: AgentId,
        *,
        trigger: ActivationTrigger | None = None,
    ) -> None:
        """Create and start the agent if it doesn't exist yet.

        Walks the lifecycle DORMANT → ACTIVATING → ACTIVE. If a lease
        registry is configured, acquires the lease before allocating the
        mailbox and spawning the loop; failure raises
        :class:`LeaseAcquisitionFailed` so the caller can route elsewhere.
        """
        if agent_id in self._agents_started:
            return

        if agent_id.type not in self._handlers:
            raise AgentNotFoundError(
                f"no handler registered for agent type {agent_id.type!r}"
            )

        trigger = trigger or ActivationTrigger(
            trigger_type="message", source_id="<unknown>"
        )

        # Transition: DORMANT → ACTIVATING
        self._activations[agent_id] = AgentActivationContract(
            lifecycle_state=AgentLifecycleState.ACTIVATING,
            trigger=trigger,
        )

        # Acquire the lease before any work; fail loud if contested.
        lease: ExecutionLease | None = None
        if self._lease_registry is not None:
            result = await self._lease_registry.acquire(
                agent_id,
                self._worker_id,
                ttl_seconds=self._lease_ttl_seconds,
            )
            if not result.acquired:
                # Roll back the ACTIVATING state we just installed.
                self._activations.pop(agent_id, None)
                holder = result.current_holder
                holder_worker = holder.worker_id if holder is not None else ""
                raise LeaseAcquisitionFailed(
                    str(agent_id),
                    current_holder_worker_id=holder_worker,
                )
            lease = result.lease

        # Create mailbox and register with dispatcher
        mailbox = Mailbox(
            capacity=self._mailbox_capacity,
            policy=self._mailbox_policy,
        )
        self._dispatcher.register_agent(agent_id, mailbox)
        self._agents_started.add(agent_id)

        # Start supervised message loop
        handler = self._handlers[agent_id.type]
        self._supervisor.supervise(
            agent_id,
            lambda aid=agent_id, mb=mailbox, h=handler: self._agent_loop(aid, mb, h),
        )

        # Transition: ACTIVATING → ACTIVE
        self._activations[agent_id] = AgentActivationContract(
            lifecycle_state=AgentLifecycleState.ACTIVE,
            trigger=trigger,
            lease=lease,
        )

        logger.debug(
            "agent %s active on %s (lease=%s)",
            agent_id,
            self._worker_id,
            lease.lease_id if lease is not None else "<none>",
        )

    async def hibernate(self, agent_id: AgentId) -> None:
        """Transition an active agent to DORMANT, releasing its lease.

        The agent's mailbox is drained and closed, its loop cancelled, and
        any held lease released. Subsequent sends will re-activate the
        agent (possibly on a different worker).
        """
        if agent_id not in self._agents_started:
            return

        current = self._activations.get(agent_id)
        if current is not None:
            # ACTIVE → HIBERNATING
            self._activations[agent_id] = AgentActivationContract(
                lifecycle_state=AgentLifecycleState.HIBERNATING,
                trigger=current.trigger,
                lease=current.lease,
                last_checkpoint=current.last_checkpoint,
                depth=current.depth,
                max_depth=current.max_depth,
            )

        # Close the mailbox to break the agent loop.
        mbox = self._dispatcher.get_mailbox(agent_id)
        if mbox is not None:
            mbox.close()

        # Release the lease so other workers can host this agent.
        if self._lease_registry is not None and current and current.lease is not None:
            await self._lease_registry.release(current.lease)

        self._agents_started.discard(agent_id)
        self._dispatcher.unregister_agent(agent_id)
        # HIBERNATING → DORMANT (record terminal state then drop)
        self._activations.pop(agent_id, None)
        logger.debug("agent %s hibernated on %s", agent_id, self._worker_id)

    def lifecycle_state(self, agent_id: AgentId) -> AgentLifecycleState:
        """Return the current :class:`AgentLifecycleState` for *agent_id*.

        Returns ``DORMANT`` when no activation contract is present.
        """
        contract = self._activations.get(agent_id)
        if contract is None:
            return AgentLifecycleState.DORMANT
        return contract.lifecycle_state

    def activation_contract(self, agent_id: AgentId) -> AgentActivationContract | None:
        """Return the current :class:`AgentActivationContract`, or ``None``."""
        return self._activations.get(agent_id)

    async def _release_all_leases(self) -> None:
        if self._lease_registry is None:
            return
        contracts = list(self._activations.items())
        for _, contract in contracts:
            if contract.lease is not None:
                try:
                    await self._lease_registry.release(contract.lease)
                except Exception:
                    logger.exception("failed to release lease on stop")

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
        """Normalize any message input to ``list[ContentBlock]``.

        Accepts:
        - ``list[ContentBlock]`` → pass through (validated element-wise)
        - ``list[str]``          → each string wrapped in a ``TextBlock``
        - mixed list             → non-block elements wrapped in ``TextBlock``
        - ``str``                → wrap in ``[TextBlock]``
        - ``StreamEnvelope``     → passed through as-is (fabric streaming contract)
        - any other object       → wrap in ``[TextBlock(text=str(...))]``

        Validation is strict: each list element must be a real
        :data:`ContentBlock` (member of :data:`CONTENT_BLOCK_TYPES`) or it is
        coerced to ``TextBlock`` here. Duck-typed objects exposing ``.type``
        and ``.model_dump`` no longer slip through — only concrete kernel
        block types are accepted as-is.
        """
        from ravi.fabric.actors.actor import (
            StreamEnvelope,
        )  # avoid top-level cycle risk

        if isinstance(message, StreamEnvelope):
            return [message]  # type: ignore[return-value]

        if isinstance(message, list):
            return [
                block
                if isinstance(block, CONTENT_BLOCK_TYPES)
                or isinstance(block, StreamEnvelope)
                else TextBlock(text=block if isinstance(block, str) else str(block))
                for block in message
            ]
        if isinstance(message, str):
            return [TextBlock(text=message)]
        return [TextBlock(text=str(message))]

    # -- introspection ------------------------------------------------------

    @property
    def active_agents(self) -> list[AgentId]:
        return list(self._agents_started)

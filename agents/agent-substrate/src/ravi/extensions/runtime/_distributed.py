"""DistributedRuntime — cross-worker AgentRuntime over EventFabric.

Composes:

- one local :class:`LocalRuntime` per worker (handles lifecycle/saga/locks)
- a shared :class:`LeaseRegistry`  (decides who owns each agent)
- a shared :class:`EventFabric`    (carries envelopes between workers)

Routing rule
~~~~~~~~~~~~
``send_message(recipient=aid)``:

- If this worker owns ``aid`` (acquired lease via the local runtime) →
  dispatch locally.
- If another worker owns it → serialise the envelope through
  :meth:`Envelope.to_event_envelope` and publish to that worker's inbox
  topic. The remote worker receives, deserialises, dispatches locally,
  and posts a reply envelope back to the originating worker.

Subscription pattern
~~~~~~~~~~~~~~~~~~~~
Each worker realtime-subscribes to two patterns:

- ``runtime.envelopes.<worker_id>`` — forward requests addressed to us
- ``runtime.replies.<worker_id>``   — replies to our outstanding sends

Topics carry partition keys so per-agent message ordering is preserved
end to end.

This is the **proof** that the kernel contracts compose; it is small on
purpose. Production deployments swap ``InMemoryEventFabric`` for
``RedisStreamsDurableLog + RedisPubSubFanout`` without changing this code.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any, Optional

from ravi.kernel.contracts._event import EventEnvelope
from ravi.kernel.scheduler._contracts import ResourceClaim, SlotGrantStatus, SchedulerContract
from ravi.kernel.events._fabric import (
    EventDeliveryMode,
    EventFabric,
    PublishRequest,
    SubscribeRequest,
)
from ravi.kernel.messages.content import ContentBlock
from ravi.kernel.runtime import (
    AgentId,
    BaseRuntime,
    BackpressurePolicy,
    CancellationToken,
    DEFAULT_LEASE_TTL_SECONDS,
    Envelope,
    LeaseAcquisitionFailed,
    LeaseRegistry,
    LocalRuntime,
    MessageHandler,
    RestartPolicy,
    TopicId,
)

if TYPE_CHECKING:
    from ravi.kernel.economic._ledger import BudgetLedger
    from ravi.kernel.governance._contracts import QuarantineActuator
    from ravi.kernel.metadata._store import MetadataStore
    from ravi.kernel.observability._killswitch import OperatorKillSwitch
    from ravi.kernel.observability._spans import EnvelopeSpanRecorder
    from ravi.kernel.safeguards._breaker import CircuitBreaker
    from ravi.kernel.semantics._contracts import SemanticInvariantChecker
    from ravi.kernel.control_plane._contracts import HotCache, RegionRegistry

logger = logging.getLogger(__name__)

__all__ = ["DistributedRuntime"]


_FORWARD_EVENT_TYPE = "runtime.forward"
_REPLY_EVENT_TYPE = "runtime.reply"
_BROADCAST_EVENT_TYPE = "runtime.broadcast"

# Where this worker listens for forward requests addressed to it.
def _inbox_topic(worker_id: str) -> str:
    return f"runtime.envelopes.{worker_id}"


# Where this worker listens for replies to its outstanding sends.
def _reply_topic(worker_id: str) -> str:
    return f"runtime.replies.{worker_id}"


# Where every worker listens for cross-worker publish_message broadcasts.
def _broadcast_topic(topic: TopicId) -> str:
    return f"runtime.broadcast.{topic.type}.{topic.source}"


class DistributedRuntime(BaseRuntime):
    """Multi-worker :class:`AgentRuntime` proof-of-composition.

    Parameters
    ----------
    fabric:
        Shared :class:`EventFabric`. Both workers must point to the same
        substrate for envelopes to flow.
    lease_registry:
        Shared :class:`LeaseRegistry`. Decides who hosts each agent.
    worker_id:
        Stable identifier; used in inbox / reply topic names. Defaults to a
        random hex string.
    remote_send_timeout:
        Seconds to wait for a reply to a cross-worker ``send_message``.
        ``None`` disables the timeout.
    restart_policy / mailbox_capacity / mailbox_policy / send_timeout /
    resource_lock_timeout / client_sink / saga_store / lease_ttl_seconds:
        Passed through to the inner :class:`LocalRuntime`.
    """

    __slots__ = (
        "_fabric",
        "_local",
        "_pending_remote_responses",
        "_inbox_task",
        "_reply_task",
        "_remote_send_timeout",
        "_inbox_ready",
        "_reply_ready",
        # Optional plane-wide service handles (S7–S16)
        "_budget_ledger",
        "_circuit_breaker",
        "_hot_cache",
        "_kill_switch",
        "_metadata_store",
        "_quarantine_actuator",
        "_region_registry",
        "_scheduler",
        "_semantic_checker",
        "_span_recorder",
    )

    def __init__(
        self,
        *,
        fabric: EventFabric,
        lease_registry: LeaseRegistry,
        worker_id: Optional[str] = None,
        remote_send_timeout: float | None = 30.0,
        restart_policy: RestartPolicy | None = None,
        mailbox_capacity: int = 100,
        mailbox_policy: BackpressurePolicy = BackpressurePolicy.SHED,
        send_timeout: float | None = 30.0,
        resource_lock_timeout: float | None = 30.0,
        lease_ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
        # S7 — economic scheduler
        budget_ledger: BudgetLedger | None = None,
        scheduler: SchedulerContract | None = None,
        # S8 — metadata store (callers read/write via .metadata_store property)
        metadata_store: MetadataStore | None = None,
        # S11 — governance: quarantine routing gate
        quarantine_actuator: QuarantineActuator | None = None,
        # S12 — self-evolution: per-principal circuit breaker
        circuit_breaker: CircuitBreaker | None = None,
        # S14 — observability: envelope span recorder + operator kill switch
        span_recorder: EnvelopeSpanRecorder | None = None,
        kill_switch: OperatorKillSwitch | None = None,
        # S15 — semantic invariant checker (callers invoke via .semantic_checker)
        semantic_checker: SemanticInvariantChecker | None = None,
        # S16 — control plane: hot cache + region registry
        hot_cache: HotCache | None = None,
        region_registry: RegionRegistry | None = None,
    ) -> None:
        super().__init__(lease_registry=lease_registry, worker_id=worker_id)
        self._fabric = fabric
        self._local = LocalRuntime(
            lease_registry=lease_registry,
            worker_id=self._worker_id,
            restart_policy=restart_policy,
            mailbox_capacity=mailbox_capacity,
            mailbox_policy=mailbox_policy,
            send_timeout=send_timeout,
            resource_lock_timeout=resource_lock_timeout,
            lease_ttl_seconds=lease_ttl_seconds,
        )
        self._pending_remote_responses: dict[str, asyncio.Future[object]] = {}
        self._inbox_task: asyncio.Task[None] | None = None
        self._reply_task: asyncio.Task[None] | None = None
        self._remote_send_timeout = remote_send_timeout
        self._inbox_ready: asyncio.Event | None = None
        self._reply_ready: asyncio.Event | None = None

        # Optional plane-wide service handles
        self._budget_ledger = budget_ledger
        self._scheduler = scheduler
        self._circuit_breaker = circuit_breaker
        self._hot_cache = hot_cache
        self._kill_switch = kill_switch
        self._metadata_store = metadata_store
        self._quarantine_actuator = quarantine_actuator
        self._region_registry = region_registry
        self._semantic_checker = semantic_checker
        self._span_recorder = span_recorder

        # Wire quarantine actuator into the local routing middleware so
        # envelopes from quarantined principals are blocked at the gate.
        if quarantine_actuator is not None:
            from ravi.extensions.runtime._middleware import QuarantineCheckMiddleware
            self._local.add_routing_middleware(
                QuarantineCheckMiddleware(quarantine_actuator=quarantine_actuator)
            )

    # -- subsystem accessors -----------------------------------------------

    @property
    def local(self) -> LocalRuntime:
        """The wrapped local runtime — exposed for tests and observers."""
        return self._local

    @property
    def fabric(self) -> EventFabric:
        return self._fabric

    @property
    def budget_ledger(self) -> BudgetLedger | None:
        return self._budget_ledger

    @property
    def hot_cache(self) -> HotCache | None:
        return self._hot_cache

    @property
    def kill_switch(self) -> OperatorKillSwitch | None:
        return self._kill_switch

    @property
    def metadata_store(self) -> MetadataStore | None:
        return self._metadata_store

    @property
    def quarantine_actuator(self) -> QuarantineActuator | None:
        return self._quarantine_actuator

    @property
    def region_registry(self) -> RegionRegistry | None:
        return self._region_registry

    @property
    def scheduler(self) -> SchedulerContract | None:
        return self._scheduler

    @property
    def semantic_checker(self) -> SemanticInvariantChecker | None:
        return self._semantic_checker

    @property
    def span_recorder(self) -> EnvelopeSpanRecorder | None:
        return self._span_recorder

    # -- BaseRuntime overrides ---------------------------------------------

    async def register(self, agent_type: str, handler: MessageHandler) -> None:
        await self._local.register(agent_type, handler)
        # Mirror into our own table so introspection is consistent.
        await super().register(agent_type, handler)

    async def subscribe(self, agent_type: str, topic: TopicId) -> None:
        await self._local.subscribe(agent_type, topic)
        await super().subscribe(agent_type, topic)

    async def start(self) -> None:
        if self._started:
            return
        await self._local.start()
        # Subscribe to our forward-request inbox and our reply inbox.
        loop = asyncio.get_running_loop()
        self._inbox_ready = asyncio.Event()
        self._reply_ready = asyncio.Event()
        self._inbox_task = loop.create_task(
            self._consume_inbox(), name=f"distributed-inbox:{self._worker_id}"
        )
        self._reply_task = loop.create_task(
            self._consume_replies(), name=f"distributed-replies:{self._worker_id}"
        )
        # Block until both subscribers have registered — otherwise the very
        # first cross-worker publish can land before the inbox listens and
        # the realtime fanout silently drops it. With Redis Pub/Sub the
        # subscription is established synchronously in the client; here we
        # rebuild that invariant explicitly.
        await asyncio.wait_for(
            asyncio.gather(self._inbox_ready.wait(), self._reply_ready.wait()),
            timeout=5.0,
        )
        self._started = True
        logger.info("DistributedRuntime %s started", self._worker_id)

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        for task in (self._inbox_task, self._reply_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._inbox_task, self._reply_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        # Fail any outstanding remote responses.
        for cid, fut in self._pending_remote_responses.items():
            if not fut.done():
                fut.cancel()
        self._pending_remote_responses.clear()
        await self._local.stop()
        logger.info("DistributedRuntime %s stopped", self._worker_id)

    async def send_message(
        self,
        message: object,
        *,
        sender: AgentId | None = None,
        recipient: AgentId,
        cancellation_token: CancellationToken | None = None,
    ) -> object:
        if not self._started:
            await self.start()

        # S14: operator kill-switch check — block before any side effects.
        if self._kill_switch is not None:
            from ravi.kernel.observability._killswitch import KillSwitchTarget
            ks_target = KillSwitchTarget(
                sender=str(sender) if sender is not None else None,
                target=str(recipient),
            )
            decision = await self._kill_switch.check(ks_target)
            if decision.blocked:
                from ravi.kernel.runtime._middleware import DropEnvelope
                raise DropEnvelope(
                    "kill_switch",
                    decision.reason or f"blocked for target {recipient}",
                )

        # S12: circuit breaker check — reject senders whose circuit is open.
        if self._circuit_breaker is not None and sender is not None:
            await self._circuit_breaker.allow_request(str(sender))

        # S14: start an envelope span to trace this dispatch.
        span_id: str | None = None
        if self._span_recorder is not None:
            from ravi.kernel.observability._spans import EnvelopeSpan, SpanStatus
            span = EnvelopeSpan(
                envelope_id=uuid.uuid4().hex,
                correlation_id=uuid.uuid4().hex,
                name="send_message",
                sender=str(sender) if sender is not None else None,
                target=str(recipient),
            )
            span = await self._span_recorder.start_span(span)
            span_id = span.span_id

        # S7: Resource Scheduler integration
        grant_id: str | None = None
        reservation = None
        if self._scheduler is not None:
            from ravi.kernel.scheduler._contracts import ResourceClaim, SlotGrantStatus
            
            sender_fqn = str(sender) if sender is not None else "anonymous"
            token_budget = int(getattr(message, "token_budget", 0) or 0)
            step_budget = int(getattr(message, "step_budget", 0) or 0)
            
            # --- Budget Ledger Spend Check ---
            if self._budget_ledger is not None and sender is not None and token_budget > 0:
                from ravi.kernel.economic._ledger import BudgetExhausted
                try:
                    reservation = await self._budget_ledger.reserve(sender, float(token_budget))
                except BudgetExhausted as exc:
                    raise ValueError(f"Scheduler denied slot request for {sender}: {exc}") from exc
                    
            # --- Placement Hint Extraction ---
            placement_region = None
            placement = getattr(message, "placement", None)
            if placement is not None:
                placement_region = getattr(placement, "region", None)

            claim = ResourceClaim(
                principal_fqn=sender_fqn,
                gpu_required=bool(getattr(message, "gpu_required", False)),
                priority=int(getattr(message, "priority", 0)),
                token_budget=token_budget,
                step_budget=step_budget,
                placement_region=placement_region,
            )
            grant = await self._scheduler.request_slot(claim)
            if grant.status == SlotGrantStatus.DENIED:
                if reservation is not None and self._budget_ledger is not None:
                    await self._budget_ledger.release(reservation)
                raise ValueError(f"Scheduler denied slot request for {sender}")
            elif grant.status == SlotGrantStatus.QUEUED:
                logger.info("Claim queued for grant %s; waiting...", grant.grant_id)
                await self._scheduler.wait_for_slot(grant.grant_id)
            grant_id = grant.grant_id

        # Optimistic local path: if we own (or can acquire) the lease, run
        # the message locally without touching the fabric.
        try:
            result = await self._try_send(
                message,
                sender=sender,
                recipient=recipient,
                cancellation_token=cancellation_token,
            )
        except Exception:
            if span_id is not None and self._span_recorder is not None:
                from ravi.kernel.observability._spans import SpanStatus
                await self._span_recorder.finish_span(span_id, status=SpanStatus.FAILED)
            if reservation is not None and self._budget_ledger is not None:
                await self._budget_ledger.release(reservation)
            raise
        else:
            if reservation is not None and self._budget_ledger is not None:
                await self._budget_ledger.commit(reservation)
        finally:
            if grant_id is not None and self._scheduler is not None:
                await self._scheduler.release_slot(grant_id)

        if span_id is not None and self._span_recorder is not None:
            from ravi.kernel.observability._spans import SpanStatus
            await self._span_recorder.finish_span(span_id, status=SpanStatus.OK)
        return result

    async def _try_send(
        self,
        message: object,
        *,
        sender: AgentId | None,
        recipient: AgentId,
        cancellation_token: CancellationToken | None,
    ) -> object:
        try:
            return await self._local.send_message(
                message,
                sender=sender,
                recipient=recipient,
                cancellation_token=cancellation_token,
            )
        except LeaseAcquisitionFailed as exc:
            return await self._forward_remote(
                message,
                sender=sender,
                recipient=recipient,
                holder_worker_id=exc.current_holder_worker_id,
                cancellation_token=cancellation_token,
            )

    async def publish_message(
        self,
        message: object,
        *,
        sender: AgentId | None = None,
        topic: TopicId,
    ) -> None:
        if not self._started:
            await self.start()
        # Always emit to the cross-worker broadcast topic so peers observe
        # this publish; the inbox consumer dispatches locally on each peer.
        content = LocalRuntime._normalize_content(message)
        envelope = Envelope(
            sender=sender,
            target=topic,
            content=content,
            event_type=_BROADCAST_EVENT_TYPE,
        )
        wire = envelope.to_event_envelope().model_dump(mode="json")
        await self._fabric.emit(
            PublishRequest(
                topic=_broadcast_topic(topic),
                partition_key=topic.source or topic.type,
                delivery_mode=EventDeliveryMode.REALTIME_FANOUT,
            ),
            wire,
        )

    # -- introspection helpers ---------------------------------------------

    def lifecycle_state(self, agent_id: AgentId):
        return self._local.lifecycle_state(agent_id)

    async def hibernate(self, agent_id: AgentId) -> None:
        await self._local.hibernate(agent_id)

    # -- internal: remote forward -----------------------------------------

    async def _forward_remote(
        self,
        message: object,
        *,
        sender: AgentId | None,
        recipient: AgentId,
        holder_worker_id: str,
        cancellation_token: CancellationToken | None,
    ) -> object:
        """Forward a send to the worker that currently holds the agent's lease."""
        if not holder_worker_id:
            raise LeaseAcquisitionFailed(
                str(recipient),
                current_holder_worker_id="",
                message=(
                    "lease contended but holder worker_id unknown; "
                    "cannot forward without a destination"
                ),
            )

        content = LocalRuntime._normalize_content(message)
        envelope = Envelope(
            sender=sender,
            target=recipient,
            content=content,
            event_type=_FORWARD_EVENT_TYPE,
            metadata={
                "reply_to_worker": self._worker_id,
                "remote_correlation_id": uuid.uuid4().hex,
                # The wire envelope drops the in-process target; stash it
                # in metadata so the remote worker can reconstruct it.
                "target": {"type": recipient.type, "key": recipient.key},
            },
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        cid = envelope.metadata["remote_correlation_id"]
        self._pending_remote_responses[cid] = future

        if cancellation_token is not None:
            cancellation_token.link_future(future)

        wire = envelope.to_event_envelope().model_dump(mode="json")
        try:
            await self._fabric.emit(
                PublishRequest(
                    topic=_inbox_topic(holder_worker_id),
                    partition_key=str(recipient),
                    delivery_mode=EventDeliveryMode.REALTIME_FANOUT,
                ),
                wire,
            )
        except Exception:
            self._pending_remote_responses.pop(cid, None)
            if not future.done():
                future.cancel()
            raise

        try:
            if self._remote_send_timeout is not None:
                return await asyncio.wait_for(
                    future, timeout=self._remote_send_timeout
                )
            return await future
        except asyncio.TimeoutError:
            self._pending_remote_responses.pop(cid, None)
            if not future.done():
                future.cancel()
            raise TimeoutError(
                f"remote send to worker {holder_worker_id} for {recipient} "
                f"timed out after {self._remote_send_timeout}s"
            ) from None

    # -- internal: inbox consumer ------------------------------------------

    async def _consume_inbox(self) -> None:
        request = SubscribeRequest(
            topic_pattern=_inbox_topic(self._worker_id),
            subscriber_id=f"inbox-{self._worker_id}",
        )
        # Pre-register the subscription so the realtime fanout sees us
        # before any peer publishes. Falls back to the async-iterator
        # protocol when the fabric impl doesn't expose the hook.
        await self._register_realtime_subscriber(request, self._inbox_ready)
        try:
            async for topic, payload in self._fabric.fanout.subscribe(request):
                try:
                    await self._handle_inbox_envelope(payload)
                except Exception:
                    logger.exception("inbox dispatch raised, dropping envelope")
        except asyncio.CancelledError:
            pass

    async def _handle_inbox_envelope(self, payload: dict[str, Any]) -> None:
        wire = EventEnvelope[list[ContentBlock]].model_validate(payload)
        # Forward request: dispatch locally, then publish a reply.
        if wire.event_type == _FORWARD_EVENT_TYPE:
            await self._dispatch_forward_and_reply(wire)
        elif wire.event_type == _BROADCAST_EVENT_TYPE:
            await self._dispatch_broadcast(wire)
        else:
            logger.debug(
                "ignoring inbox envelope %s with unknown event_type %s",
                wire.event_id,
                wire.event_type,
            )

    async def _dispatch_forward_and_reply(
        self, wire: EventEnvelope[list[ContentBlock]]
    ) -> None:
        env = wire.to_runtime_envelope()
        # The wire envelope strips the in-process target; rebuild it from
        # the metadata stashed by ``_forward_remote``.
        target_meta = wire.metadata.get("target")
        if not isinstance(target_meta, dict):
            logger.warning(
                "forward envelope %s missing target metadata; dropping",
                env.correlation_id,
            )
            return
        recipient = AgentId(type=target_meta["type"], key=target_meta["key"])
        reply_to = wire.metadata.get("reply_to_worker", "")
        remote_cid = wire.metadata.get("remote_correlation_id", "")

        result: object = None
        error: str | None = None
        try:
            result = await self._local.send_message(
                env.content,
                sender=env.sender,
                recipient=recipient,
            )
        except Exception as exc:
            logger.exception(
                "remote-forwarded send raised on %s", self._worker_id
            )
            error = repr(exc)

        if not reply_to:
            return
        reply_envelope = Envelope(
            sender=None,
            target=recipient,
            content=[],
            event_type=_REPLY_EVENT_TYPE,
            metadata={
                "remote_correlation_id": remote_cid,
                "result_repr": "" if result is None else repr(result),
                "error_repr": error or "",
            },
        )
        reply_wire = reply_envelope.to_event_envelope().model_dump(mode="json")
        await self._fabric.emit(
            PublishRequest(
                topic=_reply_topic(reply_to),
                partition_key=remote_cid or str(recipient),
                delivery_mode=EventDeliveryMode.REALTIME_FANOUT,
            ),
            reply_wire,
        )

    async def _dispatch_broadcast(
        self, wire: EventEnvelope[list[ContentBlock]]
    ) -> None:
        env = wire.to_runtime_envelope()
        # The original target was a TopicId; on the wire we lose the
        # concrete target object. Reconstruct from the wire topic name.
        # For now, dispatch as a publish on the inner local runtime using
        # the topic carried in metadata, or skip if absent.
        topic_meta = env.metadata.get("topic")
        if not isinstance(topic_meta, dict):
            return
        topic = TopicId(type=topic_meta["type"], source=topic_meta["source"])
        await self._local.publish_message(
            env.content, sender=env.sender, topic=topic
        )

    # -- internal: reply consumer ------------------------------------------

    async def _consume_replies(self) -> None:
        request = SubscribeRequest(
            topic_pattern=_reply_topic(self._worker_id),
            subscriber_id=f"replies-{self._worker_id}",
        )
        await self._register_realtime_subscriber(request, self._reply_ready)
        try:
            async for topic, payload in self._fabric.fanout.subscribe(request):
                try:
                    await self._handle_reply_envelope(payload)
                except Exception:
                    logger.exception("reply dispatch raised, dropping envelope")
        except asyncio.CancelledError:
            pass

    async def _register_realtime_subscriber(
        self,
        request: SubscribeRequest,
        ready: asyncio.Event | None,
    ) -> None:
        """Eagerly register the realtime subscriber via the fabric hook.

        The in-memory fabric (and any production backend like Redis Pub/Sub)
        exposes a ``register_subscriber`` sync method that pre-registers the
        subscriber so concurrent publishes are not lost. When absent, we
        fall back to a brief sleep that lets the async generator run its
        registration step.
        """
        register = getattr(self._fabric.fanout, "register_subscriber", None)
        if callable(register):
            register(request)
        else:  # pragma: no cover — exercised only against ad-hoc backends
            await asyncio.sleep(0.05)
        if ready is not None:
            ready.set()

    async def _handle_reply_envelope(self, payload: dict[str, Any]) -> None:
        wire = EventEnvelope[list[ContentBlock]].model_validate(payload)
        if wire.event_type != _REPLY_EVENT_TYPE:
            return
        cid = wire.metadata.get("remote_correlation_id", "")
        future = self._pending_remote_responses.pop(cid, None)
        if future is None or future.done():
            return
        error = wire.metadata.get("error_repr", "")
        if error:
            future.set_exception(RuntimeError(f"remote handler raised: {error}"))
        else:
            future.set_result(wire.metadata.get("result_repr", ""))

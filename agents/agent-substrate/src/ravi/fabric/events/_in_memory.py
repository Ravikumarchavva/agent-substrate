"""In-process reference implementations of the kernel event fabric.

These satisfy the kernel Protocols from :mod:`ravi.kernel.events._fabric`
without any external infrastructure. They are the building blocks for:

- single-process tests of cross-worker behavior
- local development of distributed-runtime code paths
- mocking the fabric in unit tests for higher-level extensions

Production deployments swap in :class:`integrations.events.RedisStreamsDurableLog`
and :class:`integrations.events.RedisPubSubFanout` (or equivalent) without
changing any caller code — they implement the same Protocols.

Thread-safety
~~~~~~~~~~~~~
The durable log shards (one per topic+partition) and the fanout subscriber
table are mutated by publishers and consumers running on different tasks,
threads, or asyncio loops. A single ``threading.RLock`` guards every
mutation; reads outside the lock window operate on locally-snapshotted
references.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import threading
import uuid
from collections import defaultdict
from typing import Any, AsyncIterator

from ravi.fabric.events._protocols import (
    AckRequest,
    ConsumeRequest,
    DurableEventLog,
    EventDeliveryMode,
    PublishRequest,
    RealtimeFanout,
    SubscribeRequest,
)

__all__ = [
    "InMemoryDurableLog",
    "InMemoryRealtimeFanout",
    "InMemoryEventFabric",
]


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Durable log
# ---------------------------------------------------------------------------


class _LogShard:
    """One ordered append-only history per ``(topic, partition_key)``."""

    __slots__ = ("entries",)

    def __init__(self) -> None:
        # (message_id, payload) ordered by append time
        self.entries: list[tuple[str, dict[str, Any]]] = []


class InMemoryDurableLog:
    """In-process implementation of :class:`DurableEventLog`.

    Stores messages in a dict of shards keyed by ``(topic, partition_key)``.
    Consumer groups remember per-shard cursors so each group sees each
    message at-least-once. ``ack`` advances the cursor past acknowledged
    messages — unacked messages remain visible to the same group.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._shards: dict[tuple[str, str], _LogShard] = defaultdict(_LogShard)
        # group -> (topic, partition) -> next_index_to_read
        self._cursors: dict[str, dict[tuple[str, str], int]] = defaultdict(dict)
        # message_id -> (group, topic, partition, index) for ack lookup
        self._inflight: dict[tuple[str, str], tuple[str, str, int]] = {}

    async def publish(
        self, request: PublishRequest, payload: dict[str, Any]
    ) -> str:
        message_id = uuid.uuid4().hex
        with self._lock:
            shard = self._shards[(request.topic, request.partition_key)]
            shard.entries.append((message_id, dict(payload)))
        logger.debug(
            "durable.publish topic=%s partition=%s msg=%s",
            request.topic,
            request.partition_key,
            message_id,
        )
        return message_id

    async def consume(
        self, request: ConsumeRequest
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield up to ``max_messages`` unacked entries for this group.

        Blocks up to ``block_ms`` while waiting for new entries.
        """
        deadline = None
        if request.block_ms > 0:
            deadline = asyncio.get_running_loop().time() + (
                request.block_ms / 1000.0
            )

        yielded = 0
        async for item in self._consume_loop(request, deadline):
            yield item
            yielded += 1
            if yielded >= request.max_messages:
                return

    async def _consume_loop(
        self, request: ConsumeRequest, deadline: float | None
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        key = (request.topic, request.partition_key)
        group = request.consumer_group

        while True:
            with self._lock:
                shard = self._shards.get(key)
                cursor = self._cursors[group].setdefault(key, 0)
                if shard is not None and cursor < len(shard.entries):
                    entry = shard.entries[cursor]
                    self._cursors[group][key] = cursor + 1
                    self._inflight[(group, entry[0])] = (
                        request.topic,
                        request.partition_key,
                        cursor,
                    )
                    yield entry
                    continue

            # No data available
            if deadline is None:
                return
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                return
            await asyncio.sleep(min(0.01, deadline - now))

    async def ack(self, request: AckRequest) -> None:
        with self._lock:
            self._inflight.pop((request.consumer_group, request.message_id), None)

    async def replay_from(
        self,
        topic: str,
        partition_key: str,
        from_offset: str,
        max_messages: int = 100,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Replay historical messages from ``from_offset`` (decimal index)."""
        try:
            start = int(from_offset)
        except ValueError:
            start = 0

        with self._lock:
            shard = self._shards.get((topic, partition_key))
            snapshot = list(shard.entries) if shard is not None else []
        for entry in snapshot[start : start + max_messages]:
            yield entry


# ---------------------------------------------------------------------------
# Realtime fanout
# ---------------------------------------------------------------------------


class _Subscriber:
    __slots__ = ("subscriber_id", "topic_pattern", "queue", "max_depth")

    def __init__(
        self, subscriber_id: str, topic_pattern: str, max_depth: int
    ) -> None:
        self.subscriber_id = subscriber_id
        self.topic_pattern = topic_pattern
        self.queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(
            maxsize=max_depth
        )
        self.max_depth = max_depth


class InMemoryRealtimeFanout:
    """In-process implementation of :class:`RealtimeFanout`.

    Pattern matching uses glob semantics via :func:`fnmatch.fnmatch`.
    Slow subscribers experience drop-oldest semantics: the queue is bounded
    by ``SubscribeRequest.max_queue_depth`` and oldest events are evicted
    when full.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[str, _Subscriber] = {}

    def register_subscriber(self, request: SubscribeRequest) -> None:
        """Eagerly register a subscriber so publishes won't race the iterator.

        Production fabric backends (Redis Pub/Sub, NATS) establish their
        subscription synchronously inside the client; in-memory fabrics
        otherwise only register on the first ``__anext__`` of the async
        generator. Calling this method up front rebuilds that invariant.

        Idempotent: re-registration of the same ``subscriber_id`` is a no-op.
        """
        with self._lock:
            if request.subscriber_id in self._subscribers:
                return
            self._subscribers[request.subscriber_id] = _Subscriber(
                subscriber_id=request.subscriber_id,
                topic_pattern=request.topic_pattern,
                max_depth=request.max_queue_depth,
            )

    async def publish(
        self, request: PublishRequest, payload: dict[str, Any]
    ) -> None:
        with self._lock:
            recipients = [
                sub
                for sub in self._subscribers.values()
                if fnmatch.fnmatch(request.topic, sub.topic_pattern)
            ]

        for sub in recipients:
            item = (request.topic, dict(payload))
            try:
                sub.queue.put_nowait(item)
            except asyncio.QueueFull:
                # Drop-oldest to make space, then retry once.
                try:
                    sub.queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover
                    pass
                try:
                    sub.queue.put_nowait(item)
                except asyncio.QueueFull:  # pragma: no cover
                    logger.warning(
                        "realtime.publish lost message for %s after eviction",
                        sub.subscriber_id,
                    )

    async def subscribe(
        self, request: SubscribeRequest
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        # Honor an existing eager registration; otherwise create one now.
        with self._lock:
            sub = self._subscribers.get(request.subscriber_id)
            if sub is None:
                sub = _Subscriber(
                    subscriber_id=request.subscriber_id,
                    topic_pattern=request.topic_pattern,
                    max_depth=request.max_queue_depth,
                )
                self._subscribers[request.subscriber_id] = sub

        try:
            while True:
                item = await sub.queue.get()
                yield item
        finally:
            with self._lock:
                self._subscribers.pop(request.subscriber_id, None)

    async def unsubscribe(self, subscriber_id: str) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)


# ---------------------------------------------------------------------------
# Composite fabric
# ---------------------------------------------------------------------------


class InMemoryEventFabric:
    """In-process implementation of :class:`EventFabric`.

    Routes a single :meth:`emit` call to the durable log, the realtime
    fanout, or both, based on :class:`EventDeliveryMode`.

    Construct with provided sub-substrates or accept the defaults — defaults
    are :class:`InMemoryDurableLog` and :class:`InMemoryRealtimeFanout`.
    """

    def __init__(
        self,
        *,
        durable: DurableEventLog | None = None,
        realtime: RealtimeFanout | None = None,
    ) -> None:
        self._log: DurableEventLog = durable or InMemoryDurableLog()
        self._fanout: RealtimeFanout = realtime or InMemoryRealtimeFanout()

    @property
    def log(self) -> DurableEventLog:
        return self._log

    @property
    def fanout(self) -> RealtimeFanout:
        return self._fanout

    async def emit(
        self, request: PublishRequest, payload: dict[str, Any]
    ) -> str | None:
        mode = request.delivery_mode
        message_id: str | None = None
        if mode in (EventDeliveryMode.DURABLE_LOG, EventDeliveryMode.BOTH):
            message_id = await self._log.publish(request, payload)
        if mode in (EventDeliveryMode.REALTIME_FANOUT, EventDeliveryMode.BOTH):
            await self._fanout.publish(request, payload)
        return message_id

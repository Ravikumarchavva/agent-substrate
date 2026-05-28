"""Redis Streams backed DurableEventLog.

Implements :class:`ravi.kernel.events._fabric.DurableEventLog` using
``redis.asyncio`` Streams.  Each logical ``(topic, partition_key)`` maps to
a single Redis Stream named ``{topic}:{partition_key}``.

Delivery semantics
------------------
* at-least-once via consumer groups (``XREADGROUP`` / ``XACK``)
* In-process ``_inflight`` dict routes ``ack()`` calls to the correct stream
  key without encoding the partition in ``AckRequest.topic``.
* Consumer groups are auto-created on first ``consume()`` call (MKSTREAM).

Thread-safety
-------------
``_lock`` guards ``_client`` initialisation and ``_inflight`` mutations.
No lock is held across an ``await``.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, AsyncIterator

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from ravi.kernel.events._fabric import (
    AckRequest,
    ConsumeRequest,
    PublishRequest,
)

__all__ = ["RedisStreamsDurableLog"]

logger = logging.getLogger(__name__)

_PAYLOAD_FIELD = "payload"


class RedisStreamsDurableLog:
    """Redis Streams backed implementation of :class:`DurableEventLog`."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._url = redis_url
        self._lock = threading.RLock()
        self._client: aioredis.Redis | None = None
        # (consumer_group, message_id) -> stream_key — ack routing
        self._inflight: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _stream_key(topic: str, partition_key: str) -> str:
        return f"{topic}:{partition_key}"

    async def _redis(self) -> aioredis.Redis:
        with self._lock:
            if self._client is None:
                self._client = aioredis.from_url(
                    self._url, decode_responses=True
                )
        return self._client

    # ------------------------------------------------------------------
    # DurableEventLog protocol
    # ------------------------------------------------------------------

    async def publish(
        self, request: PublishRequest, payload: dict[str, Any]
    ) -> str:
        """Append payload to the stream; return the assigned message_id."""
        client = await self._redis()
        key = self._stream_key(request.topic, request.partition_key)
        message_id: str = await client.xadd(
            key, {_PAYLOAD_FIELD: json.dumps(payload)}
        )
        logger.debug(
            "streams.publish topic=%s partition=%s msg=%s",
            request.topic,
            request.partition_key,
            message_id,
        )
        return message_id

    async def consume(
        self, request: ConsumeRequest
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield ``(message_id, payload)`` tuples for this consumer group.

        Creates the consumer group on first call (``XGROUP CREATE … MKSTREAM``
        with id "0" so all existing messages are delivered).
        """
        client = await self._redis()
        key = self._stream_key(request.topic, request.partition_key)

        # Create consumer group — idempotent, ignore BUSYGROUP error.
        try:
            await client.xgroup_create(
                key, request.consumer_group, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        block: int | None = request.block_ms if request.block_ms > 0 else None
        results = await client.xreadgroup(
            request.consumer_group,
            request.consumer_id,
            {key: ">"},
            count=request.max_messages,
            block=block,
        )

        if results:
            for _stream, messages in results:
                for msg_id, fields in messages:
                    # Track inflight before yielding; no lock held while yielding.
                    with self._lock:
                        self._inflight[(request.consumer_group, msg_id)] = key
                    yield msg_id, json.loads(fields[_PAYLOAD_FIELD])

    async def ack(self, request: AckRequest) -> None:
        """Acknowledge delivery so the message is not redelivered."""
        with self._lock:
            key = self._inflight.pop(
                (request.consumer_group, request.message_id), None
            )
        if key is None:
            logger.warning(
                "ack: unknown inflight msg=%s group=%s",
                request.message_id,
                request.consumer_group,
            )
            return
        client = await self._redis()
        await client.xack(key, request.consumer_group, request.message_id)

    async def replay_from(
        self,
        topic: str,
        partition_key: str,
        from_offset: str,
        max_messages: int = 100,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Replay historical messages from ``from_offset`` (Redis stream ID).

        Pass ``"-"`` or ``""`` to start from the beginning of the stream.
        """
        client = await self._redis()
        key = self._stream_key(topic, partition_key)
        start = from_offset if from_offset else "-"
        entries = await client.xrange(key, min=start, max="+", count=max_messages)
        for msg_id, fields in entries:
            yield msg_id, json.loads(fields[_PAYLOAD_FIELD])

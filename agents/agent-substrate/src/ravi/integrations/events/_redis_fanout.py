"""Redis Pub/Sub backed RealtimeFanout.

Implements :class:`ravi.kernel.events._fabric.RealtimeFanout` using
``redis.asyncio`` Pub/Sub.

Delivery semantics
------------------
* Best-effort ephemeral fanout — messages published before a subscriber
  connects are not delivered (no buffering).
* Exact topic match uses ``SUBSCRIBE``; glob patterns (``*`` / ``?``) use
  ``PSUBSCRIBE``.

Cancellation
------------
Each active subscription's ``asyncio.Event`` is stored per ``subscriber_id``.
``unsubscribe(subscriber_id)`` sets that event, causing the generator loop to
exit cleanly.

Thread-safety
-------------
``_lock`` guards ``_client`` initialisation and ``_subscriptions`` mutations.
No lock is held across an ``await``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, AsyncIterator

import redis.asyncio as aioredis

from ravi.kernel.events._fabric import (
    PublishRequest,
    SubscribeRequest,
)

__all__ = ["RedisPubSubFanout"]

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_S: float = 0.05  # seconds to wait in get_message before re-checking cancel


class RedisPubSubFanout:
    """Redis Pub/Sub backed implementation of :class:`RealtimeFanout`."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._url = redis_url
        self._lock = threading.RLock()
        self._client: aioredis.Redis | None = None
        # subscriber_id -> (pubsub_object, cancel_event)
        self._subscriptions: dict[str, tuple[Any, Any]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _redis(self) -> aioredis.Redis:
        with self._lock:
            if self._client is None:
                self._client = aioredis.from_url(
                    self._url, decode_responses=True
                )
        return self._client

    # ------------------------------------------------------------------
    # RealtimeFanout protocol
    # ------------------------------------------------------------------

    async def publish(
        self, request: PublishRequest, payload: dict[str, Any]
    ) -> None:
        """Fan out payload to all subscribers on the topic channel."""
        client = await self._redis()
        await client.publish(request.topic, json.dumps(payload))
        logger.debug("pubsub.publish topic=%s", request.topic)

    async def subscribe(
        self, request: SubscribeRequest
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield ``(topic, payload)`` tuples as they arrive.

        The generator runs until cancelled via ``unsubscribe()`` or the caller
        breaks the iteration.
        """
        client = await self._redis()
        pubsub = client.pubsub()

        is_pattern = "*" in request.topic_pattern or "?" in request.topic_pattern
        if is_pattern:
            await pubsub.psubscribe(request.topic_pattern)
        else:
            await pubsub.subscribe(request.topic_pattern)

        cancel_event = asyncio.Event()
        with self._lock:
            self._subscriptions[request.subscriber_id] = (pubsub, cancel_event)

        try:
            while not cancel_event.is_set():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=_POLL_TIMEOUT_S
                )
                if msg is None:
                    # Yield to the event loop so cancellation can be delivered
                    # and cooperative multitasking is preserved.  In production
                    # ``get_message(timeout=...)`` suspends inside the Redis
                    # client; in tests the mock returns immediately so we must
                    # add this explicit yield point.
                    await asyncio.sleep(0)
                    continue
                msg_type = msg.get("type")
                if msg_type not in ("message", "pmessage"):
                    continue
                channel: str = msg.get("channel") or msg.get("pattern") or ""
                raw = msg.get("data", "{}")
                yield channel, json.loads(raw)
        finally:
            with self._lock:
                self._subscriptions.pop(request.subscriber_id, None)
            try:
                if is_pattern:
                    await pubsub.punsubscribe(request.topic_pattern)
                else:
                    await pubsub.unsubscribe(request.topic_pattern)
                await pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def unsubscribe(self, subscriber_id: str) -> None:
        """Signal the subscriber's generator to stop and release its connection."""
        with self._lock:
            entry = self._subscriptions.get(subscriber_id)
        if entry is not None:
            _pubsub, cancel_event = entry
            cancel_event.set()

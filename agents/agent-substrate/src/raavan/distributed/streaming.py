"""NATSStreamingBridge — thread-scoped SSE event fan-out via NATS JetStream.

Wraps the low-level :class:`~raavan.integrations.runtime.nats.bridge.NATSBridge`
with a thread-oriented API for the distributed workflow:

- ``publish(thread_id, event)`` — emit an SSE event for a conversation
- ``subscribe(thread_id)`` — async iterator of events for a conversation

The SSE endpoint calls ``subscribe`` to fan events out to the browser.
Activities call ``publish`` inside ``ctx.run()`` to emit events during
durable execution.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import nats
    from nats.js.api import DeliverPolicy

    _HAS_NATS = True
except ImportError:
    _HAS_NATS = False

# Reuse the same subject prefix / stream as NATSBridge
_SUBJECT_PREFIX = "agent.events"
_STREAM_NAME = "AGENT_EVENTS"
_RETENTION_SECONDS = 3600


class NATSStreamingBridge:
    """Thread-scoped SSE event transport backed by NATS JetStream.

    Parameters
    ----------
    nats_url:
        NATS server URL (default ``nats://localhost:4222``).
    stream_name:
        JetStream stream name (default ``AGENT_EVENTS``).
    retention_seconds:
        Message retention period in seconds (default 3600).
    """

    def __init__(
        self,
        nats_url: str = "nats://localhost:4222",
        stream_name: str = _STREAM_NAME,
        retention_seconds: int = _RETENTION_SECONDS,
    ) -> None:
        if not _HAS_NATS:
            raise ImportError(
                "nats-py is required for NATSStreamingBridge. "
                "Install with: uv add nats-py"
            )
        self._nats_url = nats_url
        self._stream_name = stream_name
        self._retention_seconds = retention_seconds
        self._nc: Any = None
        self._js: Any = None

    async def connect(self) -> None:
        """Connect to NATS and ensure the JetStream stream exists."""
        self._nc = await nats.connect(self._nats_url)
        self._js = self._nc.jetstream()

        from nats.js.api import StreamConfig, RetentionPolicy

        expected_max_age = self._retention_seconds * 1_000_000_000  # ns

        try:
            await self._js.add_stream(
                StreamConfig(
                    name=self._stream_name,
                    subjects=[f"{_SUBJECT_PREFIX}.*"],
                    retention=RetentionPolicy.LIMITS,
                    max_age=expected_max_age,
                )
            )
            logger.info(
                "NATS stream %s ensured (retention=%ds)",
                self._stream_name,
                self._retention_seconds,
            )
        except Exception:
            # Stream may already exist (created by another service)
            logger.debug("NATS stream %s already exists", self._stream_name)

    async def disconnect(self) -> None:
        """Drain and close the NATS connection."""
        if self._nc is not None:
            await self._nc.drain()
            await self._nc.close()
            self._nc = None
            self._js = None
            logger.info("NATSStreamingBridge disconnected")

    async def publish(self, thread_id: str, event: Dict[str, Any]) -> None:
        """Publish an SSE event for a conversation thread.

        Args:
            thread_id: Conversation thread identifier (routing key).
            event: Event dict (must be JSON-serializable).
        """
        if self._js is None:
            raise RuntimeError("NATSStreamingBridge not connected")
        subject = f"{_SUBJECT_PREFIX}.{thread_id}"
        payload = json.dumps(event).encode("utf-8")
        await self._js.publish(subject, payload)

    async def subscribe(
        self,
        thread_id: str,
        *,
        deliver_policy: str = "new",
        consumer_name: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Subscribe to SSE events for a conversation thread.

        Args:
            thread_id: Conversation thread identifier.
            deliver_policy: ``"new"`` for fresh connections (default),
                ``"all"`` to replay from stream start.
            consumer_name: Optional durable consumer name.

        Yields:
            Event dicts as they arrive on the JetStream subject.
        """
        if self._js is None:
            raise RuntimeError("NATSStreamingBridge not connected")

        subject = f"{_SUBJECT_PREFIX}.{thread_id}"

        dp = DeliverPolicy.ALL if deliver_policy == "all" else DeliverPolicy.NEW

        kwargs: Dict[str, Any] = {"deliver_policy": dp}
        if consumer_name:
            kwargs["durable"] = consumer_name

        sub = await self._js.subscribe(subject, **kwargs)

        async for msg in sub.messages:
            try:
                event = json.loads(msg.data.decode("utf-8"))
                await msg.ack()
                yield event
            except Exception as exc:
                logger.warning("Failed to decode NATS message: %s", exc)
                await msg.ack()

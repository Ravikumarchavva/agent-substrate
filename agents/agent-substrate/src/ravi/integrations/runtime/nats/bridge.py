"""NATSBridge — NATS JetStream pub/sub for agent event streaming.

Ports the patterns from the former ``distributed/streaming.py``:
- Subject pattern: ``agent.events.{key}``
- JetStream stream: ``AGENT_EVENTS`` with 1-hour retention
- Durable consumers for reliable delivery

Can be used standalone for event streaming, or composed with
``GrpcRuntime`` / ``RestateRuntime`` to add durable pub/sub.

Usage::

    bridge = NATSBridge(nats_url="nats://localhost:4222")
    await bridge.connect()

    # Publish
    await bridge.publish("thread-abc", {"type": "text_delta", "content": "hi"})

    # Subscribe
    async for event in bridge.subscribe("thread-abc"):
        print(event)

    await bridge.disconnect()
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import nats
    from nats.js.api import StreamConfig, RetentionPolicy, DeliverPolicy

    _HAS_NATS = True
except ImportError:
    _HAS_NATS = False

# JetStream subject pattern
_SUBJECT_PREFIX = "agent.events"
_STREAM_NAME = "AGENT_EVENTS"
_RETENTION_SECONDS = 3600  # 1 hour

# M7: valid topic key pattern — alphanumeric, hyphens, underscores, dots
_VALID_KEY_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _validate_key(key: str) -> None:
    """Raise ``ValueError`` if *key* contains invalid characters."""
    if not key or not _VALID_KEY_RE.match(key):
        raise ValueError(f"invalid topic key {key!r}: must match [a-zA-Z0-9._-]+")


class NATSBridge:
    """NATS JetStream pub/sub bridge for agent event streaming.

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
                "nats-py is required for NATSBridge. Install with: uv add nats-py"
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

        expected_max_age = self._retention_seconds * 1_000_000_000  # nanoseconds

        # Ensure the stream exists (idempotent)
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
                "NATS JetStream stream %s ensured (retention=%ds)",
                self._stream_name,
                self._retention_seconds,
            )
        except Exception as exc:
            # H11 fix: stream may already exist — verify config matches
            logger.warning("JetStream stream setup: %s — verifying config", exc)
            try:
                info = await self._js.find_stream_info_by_subject(
                    f"{_SUBJECT_PREFIX}.*"
                )
                actual_max_age = info.config.max_age
                if actual_max_age != expected_max_age:
                    logger.error(
                        "Stream %s max_age mismatch: expected=%d actual=%d",
                        self._stream_name,
                        expected_max_age,
                        actual_max_age,
                    )
                    raise RuntimeError(
                        f"NATS stream {self._stream_name} config mismatch: "
                        f"max_age expected {expected_max_age} but got {actual_max_age}"
                    ) from exc
            except RuntimeError:
                raise
            except Exception as verify_exc:
                logger.warning("Could not verify stream config: %s", verify_exc)

    async def disconnect(self) -> None:
        """Disconnect from NATS."""
        if self._nc is not None:
            await self._nc.drain()
            await self._nc.close()
            self._nc = None
            self._js = None
            logger.info("Disconnected from NATS")

    async def publish(self, key: str, event: Dict[str, Any]) -> None:
        """Publish an event to the JetStream subject for the given key.

        Args:
            key: Routing key (typically thread_id or agent_id.key).
            event: Event dict to publish (JSON-serialized).
        """
        if self._js is None:
            raise RuntimeError("NATSBridge not connected")
        _validate_key(key)

        subject = f"{_SUBJECT_PREFIX}.{key}"
        payload = json.dumps(event).encode("utf-8")
        await self._js.publish(subject, payload)

    async def subscribe(
        self,
        key: str,
        *,
        consumer_name: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Subscribe to events for a given key.

        Yields event dicts as they arrive. Uses a push-based JetStream
        subscription with durable consumer (if ``consumer_name`` provided).

        Args:
            key: Routing key to subscribe to.
            consumer_name: Optional durable consumer name for reliable delivery.
        """
        if self._js is None:
            raise RuntimeError("NATSBridge not connected")
        _validate_key(key)

        subject = f"{_SUBJECT_PREFIX}.{key}"

        if consumer_name:
            sub = await self._js.subscribe(
                subject,
                durable=consumer_name,
                deliver_policy=DeliverPolicy.NEW,
            )
        else:
            sub = await self._js.subscribe(subject)

        async for msg in sub.messages:
            try:
                event = json.loads(msg.data.decode("utf-8"))
                await msg.ack()
                yield event
            except Exception as exc:
                logger.warning("Failed to decode NATS message: %s", exc)
                await msg.ack()  # ack to avoid redelivery of bad messages

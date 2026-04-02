"""StreamPublisher — helper for publishing streaming events via TopicId.

Wraps ``AgentRuntime.publish_message()`` to provide a clean API for
agents that emit a sequence of events (e.g. ``run_stream()`` chunks).

Usage inside an agent::

    from raavan.core.runtime._stream import StreamPublisher

    publisher = StreamPublisher(runtime, topic, sender=self.agent_id)
    await publisher.emit(TextDeltaChunk(...))
    await publisher.emit(CompletionChunk(...))
    await publisher.close()  # sends StreamDone sentinel
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from raavan.core.runtime._protocol import AgentId, AgentRuntime, TopicId
from raavan.core.runtime._types import StreamDone

logger = logging.getLogger(__name__)


class StreamPublisher:
    """Publishes events to a ``TopicId`` via the runtime.

    Uses an ``asyncio.Lock`` to prevent TOCTOU races between
    ``emit()`` and ``close()``.

    Parameters
    ----------
    runtime:
        The agent runtime to publish through.
    topic:
        Target topic for the stream events.
    sender:
        Identity of the publishing agent.
    """

    __slots__ = ("_runtime", "_topic", "_sender", "_closed", "_lock")

    def __init__(
        self,
        runtime: AgentRuntime,
        topic: TopicId,
        *,
        sender: AgentId,
    ) -> None:
        self._runtime = runtime
        self._topic = topic
        self._sender = sender
        self._closed = False
        self._lock = asyncio.Lock()

    @property
    def topic(self) -> TopicId:
        return self._topic

    async def emit(self, event: Any) -> None:
        """Publish a single event to the topic."""
        async with self._lock:
            if self._closed:
                raise RuntimeError("StreamPublisher is closed")
            await self._runtime.publish_message(
                event,
                sender=self._sender,
                topic=self._topic,
            )

    async def close(self, reason: str = "complete") -> None:
        """Send a ``StreamDone`` sentinel and mark the publisher as closed."""
        async with self._lock:
            if self._closed:
                return
            # M4 fix: only mark closed after successful publish
            try:
                await self._runtime.publish_message(
                    StreamDone(reason=reason),
                    sender=self._sender,
                    topic=self._topic,
                )
                self._closed = True
                logger.debug("Stream closed: %s (reason=%s)", self._topic, reason)
            except Exception:
                logger.exception(
                    "Failed to publish StreamDone for %s — caller can retry",
                    self._topic,
                )
                raise

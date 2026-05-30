"""User-facing visibility stream — progress events emitted by agents.

Agent↔agent communication uses :class:`~ravi.kernel.message.Message` (full
payloads, synchronous request/response).  Separately, an agent may emit a
sequence of incremental progress events so a user can *watch* what is
happening — token-by-token text deltas, reasoning traces, and a final
completion event.

Events are published to a :class:`~ravi.kernel.identity.TopicId`; the
user-boundary transport (console, SSE, WebSocket) subscribes and renders them.
:class:`StreamDone` is the end-of-stream sentinel.

Usage inside an agent::

    stream = StreamPublisher(runtime, topic=TopicId("output", session_id), sender=self.id)
    await stream.emit(TextDelta(text="Hello"))
    await stream.emit(CompletionEvent(content=[TextBlock(text="Hello, world!")]))
    await stream.close()
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ravi.kernel.content import ContentBlock
from ravi.kernel.identity import AgentId, TopicId
from ravi.kernel.protocol import AgentRuntime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stream event types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextDelta:
    """Incremental text content — emitted token-by-token."""

    text: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    """Incremental reasoning / thinking trace — emitted as the model thinks."""

    text: str


@dataclass(frozen=True, slots=True)
class CompletionEvent:
    """Final event — carries the fully assembled response content.

    ``content`` is a ``list[ContentBlock]`` rather than a provider message
    object so the stream layer stays independent of LLM wire formats.
    """

    content: list[ContentBlock]
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StreamDone:
    """End-of-stream sentinel.  Subscribers stop consuming on receipt."""

    reason: str = "complete"


# ---------------------------------------------------------------------------
# StreamPublisher
# ---------------------------------------------------------------------------


class StreamPublisher:
    """Publishes progress events to a TopicId through the runtime.

    An ``asyncio.Lock`` prevents TOCTOU races between concurrent ``emit()``
    calls and ``close()``.
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

    async def emit(self, event: object) -> None:
        """Publish a single progress event to the topic."""
        async with self._lock:
            if self._closed:
                raise RuntimeError("StreamPublisher is already closed")
            await self._runtime.publish_message(
                event,
                sender=self._sender,
                topic=self._topic,
            )

    async def close(self, reason: str = "complete") -> None:
        """Send a StreamDone sentinel and mark the publisher closed."""
        async with self._lock:
            if self._closed:
                return
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


__all__ = [
    "TextDelta",
    "ReasoningDelta",
    "CompletionEvent",
    "StreamDone",
    "StreamPublisher",
]

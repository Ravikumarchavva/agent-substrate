"""User-facing visibility stream — progress events emitted to the user.

This is the *one* place chunks exist. Agent↔agent communication is always
synchronous full messages (:mod:`ravi.kernel.runtime._message`). Separately,
an agent may emit a sequence of progress events so a user can *watch* what is
happening — what the main agent is about to say, what a sub-agent is doing
right now, or the token-by-token deltas of the final reply, exactly like a
chat UI's activity view.

These events are published to a :class:`TopicId`; the user-boundary transport
(Console / SSE) subscribes and renders them. :class:`StreamDone` is the
sentinel that marks the end of the sequence.

Usage inside an agent::

    publisher = StreamPublisher(runtime, topic, sender=self.agent_id)
    await publisher.emit(TextDeltaChunk(...))
    await publisher.emit(CompletionChunk(...))
    await publisher.close()  # sends the StreamDone sentinel
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ravi.kernel.runtime._identity import AgentId, TopicId
from ravi.kernel.runtime._protocol import AgentRuntime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StreamDone — end-of-stream sentinel
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StreamDone:
    """Published to a ``TopicId`` to signal the visibility stream has ended.

    Subscribers check ``isinstance(payload, StreamDone)`` to know when to stop
    consuming.
    """

    reason: str = "complete"


# ---------------------------------------------------------------------------
# StreamPublisher — emit progress events to a topic
# ---------------------------------------------------------------------------


class StreamPublisher:
    """Publishes progress events to a ``TopicId`` via the runtime.

    Uses an ``asyncio.Lock`` to prevent TOCTOU races between ``emit()`` and
    ``close()``.

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

    async def emit(self, event: object) -> None:
        """Publish a single progress event to the topic."""
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


__all__ = ["StreamDone", "StreamPublisher"]

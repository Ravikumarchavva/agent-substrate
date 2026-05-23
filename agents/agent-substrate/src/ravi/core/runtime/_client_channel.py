"""ClientWriteChannel — sequenced, attributed writes to the client/user.

When multiple agents (or sub-agents) produce output simultaneously, the
client receives interleaved, unordered fragments.  ``ClientWriteChannel``
solves this by:

1. **Sequencing** — every outbound frame gets a monotonic sequence number.
2. **Attribution** — every frame carries the ``source_agent_id`` so the
   client can demultiplex and render per-agent streams.
3. **Lane-based multiplexing** — each agent writes to a logical *lane*;
   the channel merges lanes in a deterministic order.
4. **Backpressure** — if the client is slow, the channel buffers up to
   ``max_pending`` frames before blocking producers.

Usage::

    channel = ClientWriteChannel(sink=sse_push, max_pending=200)
    lane = channel.open_lane("code_agent")
    await lane.write(TextBlock(text="Here is the code..."))
    await lane.write(CodeBlock(code="print('hi')", language="python"))
    await lane.close()

The channel is the *only* path for agent output to reach the client.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from ravi.core.messages.content import ContentBlock, JsonObject

logger = logging.getLogger("ravi.core.runtime.client_channel")


# ---------------------------------------------------------------------------
# Outbound frame — what the client actually receives
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClientFrame:
    """A single sequenced frame sent to the client.

    ``seq`` is a global monotonic counter across all lanes in a channel.
    ``lane_id`` identifies the producing agent / sub-agent.
    ``content`` is the multimodal payload.
    """

    seq: int
    lane_id: str
    source_agent_id: str
    content: list[ContentBlock]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: JsonObject = field(default_factory=dict)
    is_final: bool = False  # True on the last frame from this lane

    def to_dict(self) -> dict[str, object]:
        """Serialise for wire transport (SSE, WebSocket, etc.)."""
        return {
            "seq": self.seq,
            "lane_id": self.lane_id,
            "source_agent_id": self.source_agent_id,
            "content": [
                b.model_dump(mode="json") if hasattr(b, "model_dump") else str(b)
                for b in self.content
            ],
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
            "is_final": self.is_final,
        }


# Sink function type: receives frames to push to the client.
ClientSink = Callable[[ClientFrame], Awaitable[None]]


# ---------------------------------------------------------------------------
# WriteLane — per-agent writer
# ---------------------------------------------------------------------------


class WriteLane:
    """A single agent's write handle into the ``ClientWriteChannel``.

    Not thread-safe — use from one asyncio task per lane.
    """

    __slots__ = ("_channel", "_lane_id", "_agent_id", "_closed")

    def __init__(
        self,
        channel: "ClientWriteChannel",
        lane_id: str,
        agent_id: str,
    ) -> None:
        self._channel = channel
        self._lane_id = lane_id
        self._agent_id = agent_id
        self._closed = False

    @property
    def lane_id(self) -> str:
        return self._lane_id

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def closed(self) -> bool:
        return self._closed

    async def write(
        self,
        *blocks: ContentBlock,
        metadata: JsonObject | None = None,
    ) -> int:
        """Write content blocks to the client.  Returns the sequence number."""
        if self._closed:
            raise RuntimeError(f"WriteLane {self._lane_id!r} is closed")
        return await self._channel._enqueue_frame(
            lane_id=self._lane_id,
            agent_id=self._agent_id,
            content=list(blocks),
            metadata=metadata or {},
            is_final=False,
        )

    async def close(self, metadata: JsonObject | None = None) -> None:
        """Send a final frame and close this lane."""
        if self._closed:
            return
        self._closed = True
        await self._channel._enqueue_frame(
            lane_id=self._lane_id,
            agent_id=self._agent_id,
            content=[],
            metadata=metadata or {},
            is_final=True,
        )
        self._channel._remove_lane(self._lane_id)


# ---------------------------------------------------------------------------
# ClientWriteChannel
# ---------------------------------------------------------------------------


class ClientWriteChannel:
    """Multiplexed, sequenced write channel to the client.

    Parameters
    ----------
    sink:
        Async callable that pushes a ``ClientFrame`` to the client transport
        (SSE, WebSocket, gRPC stream, etc.).
    max_pending:
        Maximum frames buffered before producers block.  Provides backpressure.
    """

    __slots__ = (
        "_sink",
        "_seq_counter",
        "_lanes",
        "_lock",
        "_buffer",
        "_flush_task",
        "_running",
        "_max_pending",
    )

    def __init__(
        self,
        sink: ClientSink,
        max_pending: int = 200,
    ) -> None:
        self._sink = sink
        self._seq_counter: int = 0
        self._lanes: dict[str, WriteLane] = {}
        self._lock = asyncio.Lock()
        self._buffer: asyncio.Queue[ClientFrame] = asyncio.Queue(maxsize=max_pending)
        self._flush_task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._max_pending = max_pending

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Start the background flush loop."""
        if self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(
            self._flush_loop(), name="client-channel-flush"
        )
        logger.debug("ClientWriteChannel started")

    async def stop(self) -> None:
        """Drain the buffer and stop the flush loop."""
        self._running = False
        if self._flush_task is not None:
            # Drain remaining frames
            while not self._buffer.empty():
                frame = self._buffer.get_nowait()
                try:
                    await self._sink(frame)
                except Exception:
                    logger.exception("sink error during drain")
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        logger.debug("ClientWriteChannel stopped")

    # -- lane management -----------------------------------------------------

    def open_lane(self, agent_id: str, lane_id: str | None = None) -> WriteLane:
        """Create a new write lane for *agent_id*.

        Each agent should have exactly one lane.  If ``lane_id`` is not
        provided, it defaults to ``agent_id``.
        """
        lid = lane_id or agent_id
        if lid in self._lanes:
            raise ValueError(f"lane {lid!r} already exists")
        lane = WriteLane(self, lid, agent_id)
        self._lanes[lid] = lane
        logger.debug("opened lane %s for agent %s", lid, agent_id)
        return lane

    def get_lane(self, lane_id: str) -> WriteLane | None:
        """Return an existing lane, or None."""
        return self._lanes.get(lane_id)

    @property
    def active_lanes(self) -> list[str]:
        """Lane IDs that are still open."""
        return [lid for lid, lane in self._lanes.items() if not lane.closed]

    def _remove_lane(self, lane_id: str) -> None:
        """Internal: remove a closed lane from the registry."""
        self._lanes.pop(lane_id, None)

    # -- internal frame enqueue ----------------------------------------------

    async def _enqueue_frame(
        self,
        lane_id: str,
        agent_id: str,
        content: list[ContentBlock],
        metadata: JsonObject,
        is_final: bool,
    ) -> int:
        """Atomically assign a sequence number and enqueue a frame."""
        async with self._lock:
            self._seq_counter += 1
            seq = self._seq_counter

        frame = ClientFrame(
            seq=seq,
            lane_id=lane_id,
            source_agent_id=agent_id,
            content=content,
            metadata=metadata,
            is_final=is_final,
        )

        # This will block if the buffer is full → backpressure
        await self._buffer.put(frame)
        return seq

    # -- flush loop ----------------------------------------------------------

    async def _flush_loop(self) -> None:
        """Background task: drain buffer and push frames to the sink."""
        while self._running or not self._buffer.empty():
            try:
                frame = await asyncio.wait_for(
                    self._buffer.get(), timeout=0.1
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await self._sink(frame)
            except Exception:
                logger.exception(
                    "sink error for frame seq=%d lane=%s",
                    frame.seq, frame.lane_id,
                )
                # Frame is dropped — in production you'd want a DLQ or retry

    # -- introspection -------------------------------------------------------

    @property
    def seq_counter(self) -> int:
        """Current sequence counter value."""
        return self._seq_counter

    @property
    def pending_count(self) -> int:
        """Number of frames in the buffer waiting to be flushed."""
        return self._buffer.qsize()

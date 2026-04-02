"""Bounded async mailbox with backpressure.

Each agent instance owns one ``Mailbox``.  The mailbox is a thin wrapper
around ``asyncio.Queue`` that adds a close/sentinel protocol and a
domain-specific ``MailboxFullError``.

Close uses an ``asyncio.Event`` as secondary signal so ``get()`` never
deadlocks when the queue is full at close time.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from raavan.core.runtime._types import Envelope


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------

_MAILBOX_CLOSED = object()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MailboxFullError(Exception):
    """Raised when a non-blocking ``put_nowait`` finds the mailbox at capacity."""


# ---------------------------------------------------------------------------
# Mailbox
# ---------------------------------------------------------------------------


class Mailbox:
    """Bounded async message queue for a single agent.

    Parameters
    ----------
    capacity:
        Maximum number of envelopes the mailbox can hold before applying
        backpressure.  Defaults to 100.
    """

    __slots__ = ("_queue", "_closed", "_close_event")

    def __init__(self, capacity: int = 100) -> None:
        self._queue: asyncio.Queue[Envelope | object] = asyncio.Queue(maxsize=capacity)
        self._closed = False
        self._close_event = asyncio.Event()

    # -- producers ----------------------------------------------------------

    async def put(self, envelope: Envelope) -> None:
        """Enqueue *envelope*, blocking if the mailbox is full."""
        if self._closed:
            raise MailboxFullError("mailbox is closed")
        await self._queue.put(envelope)

    def put_nowait(self, envelope: Envelope) -> None:
        """Enqueue *envelope* without waiting.

        Raises ``MailboxFullError`` if at capacity or closed.
        """
        if self._closed:
            raise MailboxFullError("mailbox is closed")
        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            raise MailboxFullError(
                f"mailbox at capacity ({self._queue.maxsize})"
            ) from None

    # -- consumers ----------------------------------------------------------

    async def get(self, timeout: Optional[float] = None) -> Envelope:
        """Dequeue the next envelope.

        Uses ``asyncio.Event`` as secondary close signal so ``get()``
        never deadlocks when the queue is full at close time.

        Raises ``StopAsyncIteration`` when the mailbox is closed.
        Raises ``asyncio.TimeoutError`` when *timeout* expires.
        """
        # Fast path: already closed and queue empty
        if self._closed and self._queue.empty():
            raise StopAsyncIteration("mailbox closed")

        # Race queue.get() against close_event.wait()
        get_task = asyncio.ensure_future(self._queue.get())
        close_task = asyncio.ensure_future(self._close_event.wait())

        try:
            done, pending = await asyncio.wait(
                {get_task, close_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            get_task.cancel()
            close_task.cancel()
            raise

        # Cancel whichever didn't fire
        for task in pending:
            task.cancel()

        # Timeout: neither finished
        if not done:
            get_task.cancel()
            close_task.cancel()
            raise asyncio.TimeoutError("mailbox get timed out")

        # Close event fired first (or both)
        if close_task in done and get_task not in done:
            raise StopAsyncIteration("mailbox closed")

        # Got an item from the queue
        item = get_task.result()
        if item is _MAILBOX_CLOSED:
            # Re-insert so other consumers also see the sentinel
            try:
                self._queue.put_nowait(_MAILBOX_CLOSED)
            except asyncio.QueueFull:
                pass
            raise StopAsyncIteration("mailbox closed")

        return item  # type: ignore[return-value]

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Signal that no more messages will arrive.

        Sets the close event (unblocks any ``get()`` waiters) and also
        tries inserting a sentinel for consumers reading the raw queue.
        """
        if not self._closed:
            self._closed = True
            self._close_event.set()
            try:
                self._queue.put_nowait(_MAILBOX_CLOSED)
            except asyncio.QueueFull:
                pass  # close_event guarantees get() sees the close

    # -- introspection ------------------------------------------------------

    @property
    def size(self) -> int:
        return self._queue.qsize()

    @property
    def is_full(self) -> bool:
        return self._queue.full()

    @property
    def is_empty(self) -> bool:
        return self._queue.empty()

    @property
    def closed(self) -> bool:
        return self._closed

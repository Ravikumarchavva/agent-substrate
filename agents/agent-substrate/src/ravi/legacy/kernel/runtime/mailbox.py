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

from ravi.kernel.runtime._backpressure import BackpressureAction, BackpressurePolicy
from ravi.kernel.runtime._contracts import Envelope
from ravi.kernel.runtime._errors import MailboxFullError

# Re-export MailboxFullError so existing ``from _mailbox import MailboxFullError``
# continues to work.
__all__ = ["Mailbox", "MailboxFullError"]


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------

_MAILBOX_CLOSED = object()


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
    policy:
        :class:`BackpressurePolicy` applied by :meth:`put_nowait` when the
        mailbox is at capacity. Default is ``SHED``: raise
        ``MailboxFullError`` so the dispatcher can emit a
        ``BackpressureSignal`` instead of silently dropping.
    """

    __slots__ = ("_queue", "_closed", "_close_event", "_policy", "_capacity")

    def __init__(
        self,
        capacity: int = 100,
        *,
        policy: BackpressurePolicy = BackpressurePolicy.SHED,
    ) -> None:
        self._queue: asyncio.Queue[Envelope | object] = asyncio.Queue(maxsize=capacity)
        self._closed = False
        self._close_event = asyncio.Event()
        self._policy = policy
        self._capacity = capacity

    # -- producers ----------------------------------------------------------

    async def put(self, envelope: Envelope) -> None:
        """Enqueue *envelope*, blocking if the mailbox is full.

        Always uses ``BLOCK`` semantics regardless of configured ``policy`` —
        the policy only governs non-blocking ``put_nowait`` paths.
        """
        if self._closed:
            raise MailboxFullError("mailbox is closed")
        await self._queue.put(envelope)

    def put_nowait(self, envelope: Envelope) -> BackpressureAction:
        """Enqueue *envelope* without waiting; honor the configured policy.

        Returns the :class:`BackpressureAction` that was taken so the caller
        (typically the ``Dispatcher``) can emit a ``BackpressureSignal`` and
        update fabric metrics.

        Raises ``MailboxFullError`` only under ``SHED`` policy (the
        ``Dispatcher`` catches it and emits the signal).
        """
        if self._closed:
            raise MailboxFullError("mailbox is closed")
        try:
            self._queue.put_nowait(envelope)
            return BackpressureAction.ACCEPTED
        except asyncio.QueueFull:
            return self._apply_full_policy(envelope)

    def _apply_full_policy(self, envelope: Envelope) -> BackpressureAction:
        policy = self._policy
        if policy is BackpressurePolicy.SHED:
            raise MailboxFullError(
                f"mailbox at capacity ({self._capacity})"
            )
        if policy is BackpressurePolicy.DROP_NEWEST:
            # Silently discard the incoming envelope.
            return BackpressureAction.DROPPED_NEWEST
        if policy is BackpressurePolicy.DROP_OLDEST:
            # Evict the oldest envelope and retry once.
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover — full but empty?!
                pass
            try:
                self._queue.put_nowait(envelope)
            except asyncio.QueueFull:  # pragma: no cover — race after eviction
                raise MailboxFullError(
                    "mailbox lost eviction race; dropping"
                ) from None
            return BackpressureAction.DROPPED_OLDEST
        # BLOCK is not applicable to a non-blocking put — fall through to SHED.
        raise MailboxFullError(
            f"mailbox at capacity ({self._capacity}); "
            f"BLOCK policy not supported on put_nowait"
        )

    @property
    def policy(self) -> BackpressurePolicy:
        """The configured backpressure policy (applied by ``put_nowait``)."""
        return self._policy

    @property
    def capacity(self) -> int:
        """Configured maximum queue depth."""
        return self._capacity

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

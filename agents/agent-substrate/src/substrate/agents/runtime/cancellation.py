"""CancellationToken — concrete cooperative-cancellation primitive.

Split out of ``kernel/agent/runtime_context.py``: this has real asyncio
state (an ``Event``, a callback list) and real behavior, so it's a concrete
implementation, not a contract — kernel keeps only the
``CancellationToken`` Protocol (see that module), typed against this class.

Usage::

    token = CancellationToken()

    # From outside (orchestrator, timeout handler, user):
    token.cancel()

    # Inside any coroutine:
    token.check()         # raises CancellationError if cancelled
    await token.wait()    # blocks until cancelled

    # Register a callback (called synchronously on cancel):
    token.add_callback(lambda: ...)
"""

from __future__ import annotations

import asyncio
from typing import Callable

from substrate.kernel.core.errors import CancellationError


class CancellationToken:
    """Cooperative cancellation signal for agent operations.

    Pure asyncio — no I/O, no threads.
    """

    def __init__(self) -> None:
        self._cancelled = False
        self._event = asyncio.Event()
        self._callbacks: list[Callable[[], None]] = []

    def cancel(self, reason: str = "cancelled") -> None:
        """Signal cancellation. Idempotent — safe to call multiple times."""
        if not self._cancelled:
            self._cancelled = True
            self._reason = reason
            self._event.set()
            for cb in self._callbacks:
                try:
                    cb()
                except Exception:
                    pass

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def check(self) -> None:
        """Raise ``CancellationError`` if this token has been cancelled.

        Call at cooperative yield points: before LLM calls, before tool
        execution, between loop iterations.
        """
        if self._cancelled:
            raise CancellationError(getattr(self, "_reason", "cancelled"))

    async def wait(self) -> None:
        """Block until the token is cancelled."""
        await self._event.wait()

    def add_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback invoked synchronously when ``cancel()`` is called."""
        if self._cancelled:
            callback()
        else:
            self._callbacks.append(callback)

    def child(self) -> "CancellationToken":
        """Return a child token that is cancelled when this one is.

        Cancelling the child does NOT cancel the parent.
        """
        child_token = CancellationToken()
        self.add_callback(lambda: child_token.cancel("parent cancelled"))
        return child_token


__all__ = ["CancellationToken"]

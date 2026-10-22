"""Long-running operations: a `begin_<verb>` method starts the work and returns a poller; `.result()` waits for it.

poller = client.begin_import_table("main.nyc.trips", files)
print(poller.status())         # "running"
table = poller.result()        # blocks until done; raises the operation's own error if it failed
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

DONE = "done"
RUNNING = "running"
FAILED = "failed"


class OperationFailed(Exception):
    """The operation ended in failure and reported no more specific error."""


class LROPoller[T]:
    """`poll()` returns `(state, value)`: `RUNNING`, or `DONE` with the final value; it raises when the operation failed."""

    def __init__(
        self,
        poll: Callable[[], tuple[str, T | None]],
        *,
        interval: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._poll, self._interval, self._sleep = poll, interval, sleep
        self._state: str = RUNNING
        self._value: T | None = None

    def status(self) -> str:
        if self._state == RUNNING:
            self._state, self._value = self._poll()
        return self._state

    def done(self) -> bool:
        return self.status() == DONE

    def result(self, timeout: float | None = None) -> T:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.done():
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("the operation did not finish in time")
            self._sleep(self._interval)
        return self._value  # type: ignore[return-value]


class AsyncLROPoller[T]:
    def __init__(
        self,
        poll: Callable[[], Awaitable[tuple[str, T | None]]],
        *,
        interval: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._poll, self._interval, self._sleep = poll, interval, sleep
        self._state: str = RUNNING
        self._value: T | None = None

    async def status(self) -> str:
        if self._state == RUNNING:
            self._state, self._value = await self._poll()
        return self._state

    async def done(self) -> bool:
        return await self.status() == DONE

    async def result(self, timeout: float | None = None) -> T:
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while not await self.done():
            if deadline is not None and loop.time() >= deadline:
                raise TimeoutError("the operation did not finish in time")
            await self._sleep(self._interval)
        return self._value  # type: ignore[return-value]

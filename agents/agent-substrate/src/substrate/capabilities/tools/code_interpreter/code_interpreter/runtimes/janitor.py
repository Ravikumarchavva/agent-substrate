"""SandboxJanitor — reap sandboxes whose session has gone idle.

Only matters for runtimes that keep something alive between turns (today:
``K8sRuntime``, one pod per session). ``BubblewrapRuntime`` needs no janitor —
each execution is a fresh process and ``--die-with-parent`` guarantees nothing
outlives us — so ``start()`` is a no-op for runtimes without
``terminate_session``.

Follows the established background-task shape in this codebase
(``agents/runtime/worker.py``, ``capabilities/triggers/conditions.py``): an
``asyncio`` loop guarded by a ``_running`` flag, cancelled on ``stop()``. It is
deliberately *not* built on ``TriggerScheduler``, whose ``add_trigger`` API is
for user-defined triggers, not internal housekeeping.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

from substrate.logger import setup_logging

logger = setup_logging()


class _Reapable(Protocol):
    """A runtime that holds per-session resources worth reclaiming."""

    async def terminate_session(self, thread_id: str) -> None: ...


class SandboxJanitor:
    """Periodically terminate sandboxes idle longer than ``ttl_seconds``."""

    def __init__(
        self,
        runtime: Any,
        *,
        ttl_seconds: int = 3600,
        interval_seconds: int = 300,
    ) -> None:
        self._runtime = runtime
        self._ttl = ttl_seconds
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def supported(self) -> bool:
        """Whether this runtime has anything to reap."""
        return hasattr(self._runtime, "terminate_session") and hasattr(
            getattr(self._runtime, "_service", None), "store"
        )

    async def start(self) -> None:
        if not self.supported or self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="sandbox-janitor")
        logger.info(
            "Sandbox janitor started (ttl=%ds, every %ds)", self._ttl, self._interval
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                await self._sweep()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                logger.warning("Sandbox janitor sweep failed: %s", exc)

    async def _sweep(self) -> None:
        store = self._runtime._service.store  # noqa: SLF001 - owned collaborator
        cutoff = time.time() - self._ttl
        for session in store.idle_since(cutoff):
            logger.info(
                "Reaping idle sandbox for session %s (idle %.0fs)",
                session.thread_id,
                time.time() - session.last_accessed,
            )
            await self._runtime.terminate_session(session.thread_id)


__all__ = ["SandboxJanitor"]

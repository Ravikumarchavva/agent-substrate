"""Erlang-style supervisor for agent crash recovery.

Monitors ``asyncio.Task`` instances that run agent message loops.
When a task fails:

- **one_for_one** — restart only the crashed agent.
- **one_for_all** — restart every supervised agent (for tightly-coupled groups).

If the restart budget (``max_restarts`` within ``restart_window``) is
exceeded the supervisor raises ``SupervisorEscalation`` instead of
restarting further.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict

from raavan.core.runtime._protocol import AgentId
from raavan.core.runtime._types import RestartPolicy

logger = logging.getLogger("raavan.core.runtime.supervisor")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SupervisorEscalation(Exception):
    """Raised when an agent exceeds its restart budget."""


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


class Supervisor:
    """Monitors and restarts agent tasks using an Erlang-style strategy.

    Parameters
    ----------
    restart_policy:
        Controls max restart count, time window, and strategy.
    """

    __slots__ = (
        "_policy",
        "_tasks",
        "_factories",
        "_restart_times",
        "_running",
    )

    def __init__(self, restart_policy: RestartPolicy | None = None) -> None:
        self._policy = restart_policy or RestartPolicy()
        self._tasks: Dict[AgentId, asyncio.Task[Any]] = {}
        self._factories: Dict[AgentId, Callable[[], Awaitable[Any]]] = {}
        self._restart_times: Dict[AgentId, Deque[float]] = {}
        self._running = True

    # -- public API ---------------------------------------------------------

    def supervise(
        self,
        agent_id: AgentId,
        coro_factory: Callable[[], Awaitable[Any]],
    ) -> asyncio.Task[Any]:
        """Start and supervise an agent task.

        ``coro_factory`` is a zero-arg callable that returns a new coroutine
        each time — the supervisor calls it again on restarts.
        """
        self._factories[agent_id] = coro_factory
        self._restart_times.setdefault(agent_id, deque())
        return self._spawn(agent_id)

    async def stop_all(self) -> None:
        """Cancel every supervised task and wait for them to finish."""
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._factories.clear()
        self._restart_times.clear()

    # -- internal -----------------------------------------------------------

    def _spawn(self, agent_id: AgentId) -> asyncio.Task[Any]:
        factory = self._factories[agent_id]
        task = asyncio.create_task(
            self._run_supervised(agent_id, factory),
            name=f"agent:{agent_id}",
        )
        self._tasks[agent_id] = task
        return task

    async def _run_supervised(
        self,
        agent_id: AgentId,
        factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        try:
            return await factory()
        except asyncio.CancelledError:
            raise  # normal shutdown — do not restart
        except Exception as exc:
            if not self._running:
                return None
            await self._on_crash(agent_id, exc)
            return None

    async def _on_crash(self, agent_id: AgentId, error: Exception) -> None:
        """Decide whether to restart or escalate."""
        logger.warning("agent %s crashed: %s", agent_id, error)

        now = time.monotonic()
        times = self._restart_times.setdefault(agent_id, deque())

        # Prune restarts outside the window
        window_start = now - self._policy.restart_window
        while times and times[0] < window_start:
            times.popleft()

        if len(times) >= self._policy.max_restarts:
            logger.error(
                "agent %s exceeded restart budget (%d in %.0fs) — escalating",
                agent_id,
                self._policy.max_restarts,
                self._policy.restart_window,
            )
            raise SupervisorEscalation(
                f"agent {agent_id} crashed {len(times) + 1} times "
                f"within {self._policy.restart_window}s"
            )

        times.append(now)

        if self._policy.strategy == "one_for_all":
            await self._restart_all()
        else:
            self._spawn(agent_id)
            logger.info("restarted agent %s (attempt %d)", agent_id, len(times))

    async def _restart_all(self) -> None:
        """Restart every supervised agent (one_for_all strategy)."""
        logger.info("one_for_all: restarting all %d agents", len(self._tasks))
        # Cancel all current tasks
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

        # M2 fix: re-spawn each independently — one failure doesn't stop others
        agent_ids = list(self._factories.keys())
        for aid in agent_ids:
            try:
                self._spawn(aid)
            except Exception:
                logger.exception("failed to respawn agent %s during restart_all", aid)

    # -- introspection ------------------------------------------------------

    @property
    def supervised_agents(self) -> list[AgentId]:
        return list(self._tasks.keys())

    @property
    def running(self) -> bool:
        return self._running

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
import threading
import time
from collections import deque
from typing import Awaitable, Callable, Deque

from ravi.kernel.runtime._identity import AgentId
from ravi.kernel.runtime._contracts import RestartPolicy
from ravi.kernel.runtime._errors import SupervisorEscalation

logger = logging.getLogger(__name__)

# Re-export so existing ``from _supervisor import SupervisorEscalation`` works.
__all__ = ["Supervisor", "SupervisorEscalation"]


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
        "_lock",
    )

    def __init__(self, restart_policy: RestartPolicy | None = None) -> None:
        self._policy = restart_policy or RestartPolicy()
        self._tasks: dict[AgentId, asyncio.Task[object]] = {}
        self._factories: dict[AgentId, Callable[[], Awaitable[object]]] = {}
        self._restart_times: dict[AgentId, Deque[float]] = {}
        self._running = True
        # Free-threaded Python builds drop the GIL; even single-loop builds
        # touch _tasks from supervisor callbacks scheduled on other threads.
        self._lock = threading.RLock()

    # -- public API ---------------------------------------------------------

    def supervise(
        self,
        agent_id: AgentId,
        coro_factory: Callable[[], Awaitable[object]],
    ) -> asyncio.Task[object]:
        """Start and supervise an agent task.

        ``coro_factory`` is a zero-arg callable that returns a new coroutine
        each time — the supervisor calls it again on restarts.
        """
        with self._lock:
            self._factories[agent_id] = coro_factory
            self._restart_times.setdefault(agent_id, deque())
        return self._spawn(agent_id)

    async def stop_all(self) -> None:
        """Cancel every supervised task and wait for them to finish."""
        with self._lock:
            self._running = False
            tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with self._lock:
            self._tasks.clear()
            self._factories.clear()
            self._restart_times.clear()

    # -- internal -----------------------------------------------------------

    def _spawn(self, agent_id: AgentId) -> asyncio.Task[object]:
        with self._lock:
            factory = self._factories[agent_id]
        task = asyncio.create_task(
            self._run_supervised(agent_id, factory),
            name=f"agent:{agent_id}",
        )
        with self._lock:
            self._tasks[agent_id] = task
        return task

    async def _run_supervised(
        self,
        agent_id: AgentId,
        factory: Callable[[], Awaitable[object]],
    ) -> object:
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
        with self._lock:
            times = self._restart_times.setdefault(agent_id, deque())
            # Prune restarts outside the window
            window_start = now - self._policy.restart_window
            while times and times[0] < window_start:
                times.popleft()
            over_budget = len(times) >= self._policy.max_restarts
            attempt_count = len(times) + 1
            if not over_budget:
                times.append(now)

        if over_budget:
            logger.error(
                "agent %s exceeded restart budget (%d in %.0fs) — escalating",
                agent_id,
                self._policy.max_restarts,
                self._policy.restart_window,
            )
            raise SupervisorEscalation(
                f"agent {agent_id} crashed {attempt_count} times "
                f"within {self._policy.restart_window}s"
            )

        if self._policy.strategy == "one_for_all":
            await self._restart_all()
        else:
            self._spawn(agent_id)
            logger.info("restarted agent %s (attempt %d)", agent_id, attempt_count)

    async def _restart_all(self) -> None:
        """Restart every supervised agent (one_for_all strategy)."""
        current = asyncio.current_task()
        with self._lock:
            logger.info("one_for_all: restarting all %d agents", len(self._tasks))
            tasks_to_cancel = [t for t in self._tasks.values() if t is not current]
        for task in tasks_to_cancel:
            task.cancel()
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        with self._lock:
            agent_ids = list(self._factories.keys())
        for aid in agent_ids:
            try:
                self._spawn(aid)
            except Exception:
                logger.exception("failed to respawn agent %s during restart_all", aid)

    # -- introspection ------------------------------------------------------

    @property
    def supervised_agents(self) -> list[AgentId]:
        with self._lock:
            return list(self._tasks.keys())

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

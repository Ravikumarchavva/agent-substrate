"""SpawnTracker — run-level headcount authority.

``Supervision`` (kernel) carries the policy numbers (max_agents, priority).
``SpawnTracker`` (agents layer) carries the mutable state that enforces them.

What's enforced today: a shared headcount pool (``max_agents``) for the
whole run. Every agent that is spawned consumes one slot; ``release()``
returns it so temporary agents don't permanently exhaust the quota. When the
pool is full, further spawns raise ``BudgetExhaustedError`` regardless of
priority.

``Priority`` is still recorded per agent (``priority_of()``) as bookkeeping
for callers that want to reason about it, but it does not currently affect
admission — a priority-based preemption path (pausing a lower-priority
active agent to admit a higher-priority one) previously existed here but was
removed: it had zero production callers and was structurally unreachable,
since ``OrchestratorAgent`` dispatches sub-agents strictly sequentially (one
``acquire``/``spawn``/``ask``/``release`` cycle at a time), so the pool never
actually held more than one concurrently active agent for a preemption to
apply to. Re-introduce preemption only alongside genuine concurrent
dispatch, not as a decorative promise ahead of it.

Usage::

    supervision = Supervision.root(orchestrator_id, spawn_budget=SpawnBudget(max_agents=20))
    budget = SpawnTracker(supervision)

    # Before each spawn_child() call:
    budget.acquire(agent_id, priority=Priority.HIGH)   # raises on limit breach

    # When an agent's run is complete:
    budget.release(agent_id)
"""

from __future__ import annotations

import threading

from substrate.kernel.exceptions import BudgetExhaustedError
from substrate.kernel.core.identity import Actor
from substrate.kernel.agent.supervision import Priority, SpawnBudget


class SpawnTracker:
    """Thread-safe mutable headcount tracker for a single execution run."""

    def __init__(self, spawn_budget: SpawnBudget) -> None:
        self._max_agents = spawn_budget.max_agents
        self._total = 1  # root agent already counts as 1
        self._active: dict[Actor, Priority] = {}  # agent → its current priority
        self._lock = threading.Lock()

    # -- Acquisition ---------------------------------------------------------

    def acquire(self, agent_id: Actor, priority: Priority = Priority.NORMAL) -> None:
        """Reserve a slot for *agent_id*.

        Raises ``BudgetExhaustedError`` if the pool is already at
        ``max_agents``.
        """
        with self._lock:
            if self._total >= self._max_agents:
                raise BudgetExhaustedError(
                    f"Run headcount cap reached ({self._total}/{self._max_agents} agents). "
                    f"Agent '{agent_id}' cannot be spawned. Increase max_agents."
                )
            self._active[agent_id] = priority
            self._total += 1

    # -- Release -------------------------------------------------------------

    def release(self, agent_id: Actor) -> None:
        """Return *agent_id*'s slot to the pool.

        Removes from active tracking and decrements total so the freed slot
        is available for future spawns.
        """
        with self._lock:
            if agent_id in self._active:
                del self._active[agent_id]
                if self._total > 1:
                    self._total -= 1

    # -- Introspection -------------------------------------------------------

    @property
    def total_spawned(self) -> int:
        """Current total agent count in the run (including root)."""
        return self._total

    def priority_of(self, agent_id: Actor) -> Priority | None:
        """Return the current priority of *agent_id*, or None if not active."""
        with self._lock:
            return self._active.get(agent_id)

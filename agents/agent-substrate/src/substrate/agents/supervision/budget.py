"""SpawnTracker — run-level headcount authority with priority-based preemption.

``Supervision`` (kernel) carries the policy numbers (max_agents, priority).
``SpawnTracker`` (agents layer) carries the mutable state that enforces them.

Two orthogonal mechanisms:

headcount  — ``max_agents`` is a shared pool for the whole run. Every agent
             that is spawned consumes one slot; ``release()`` returns it so
             temporary agents don't permanently exhaust the quota.

priority   — When the pool is full, HIGH/CRITICAL agents can preempt
             lower-priority active agents by issuing them a cooperative pause
             signal (``_paused``). The paused agent checks ``is_paused()``
             before each LLM call and stops spawning new work. The slot is
             reallocated to the higher-priority requester without waiting for
             the victim to finish.

Usage::

    supervision = Supervision.root(orchestrator_id, spawn_budget=SpawnBudget(max_agents=20))
    budget = SpawnTracker(supervision)

    # Before each spawn_child() call:
    budget.acquire(agent_id, priority=Priority.HIGH)   # raises on limit breach

    # When an agent's run is complete:
    budget.release(agent_id)

    # Dynamic reprioritization mid-run:
    budget.reprioritize(agent_id, Priority.LOW)
"""

from __future__ import annotations

import threading

from substrate.kernel.core.errors import BudgetExhaustedError
from substrate.kernel.core.identity import AgentId
from substrate.kernel.agent.supervision import Priority, SpawnBudget


class SpawnTracker:
    """Thread-safe mutable headcount tracker for a single execution run.

    Priority is the single authority for how slots are allocated and
    reallocated. Two rules:

    1. If the pool has room, any agent gets a slot regardless of priority.
    2. If the pool is full, only HIGH/CRITICAL agents can preempt — they
       pause the lowest-priority active agent strictly below their own
       priority level. NORMAL and below raise ``BudgetExhaustedError``.
    """

    def __init__(self, spawn_budget: SpawnBudget) -> None:
        self._max_agents = spawn_budget.max_agents
        self._total = 1  # root agent already counts as 1
        self._active: dict[AgentId, Priority] = {}  # agent → its current priority
        self._paused: set[AgentId] = set()  # cooperative pause signals
        self._lock = threading.Lock()

    # -- Acquisition ---------------------------------------------------------

    def acquire(self, agent_id: AgentId, priority: Priority = Priority.NORMAL) -> None:
        """Reserve a slot for *agent_id* at *priority*.

        If the pool has room, grants the slot immediately.

        If the pool is full and *priority* is HIGH or CRITICAL, finds the
        lowest-priority active agent strictly below *priority* and adds it to
        the pause set, then grants the slot to *agent_id* (slot count stays
        the same — the victim's slot is reallocated).

        Raises ``BudgetExhaustedError`` if:
        - *priority* is NORMAL or below and the pool is full, or
        - *priority* is HIGH/CRITICAL but all active agents are at an equal
          or higher priority level (nothing can be preempted).
        """
        with self._lock:
            if self._total < self._max_agents:
                self._active[agent_id] = priority
                self._total += 1
                return

            # Pool is full.
            if priority <= Priority.NORMAL:
                raise BudgetExhaustedError(
                    f"Run headcount cap reached ({self._total}/{self._max_agents} agents). "
                    f"Agent '{agent_id}' at priority {priority.name} cannot preempt. "
                    "Use Priority.HIGH or CRITICAL, or increase max_agents."
                )

            # HIGH/CRITICAL: try to preempt the lowest-priority victim.
            candidates = [(aid, p) for aid, p in self._active.items() if p < priority]
            if not candidates:
                raise BudgetExhaustedError(
                    f"All {self._total} active agents are priority >= {priority.name}. "
                    f"Cannot preempt to make room for '{agent_id}'."
                )

            victim_id, _ = min(candidates, key=lambda x: x[1].value)
            self._paused.add(victim_id)
            # Slot is reallocated from victim to new agent (total unchanged).
            self._active[agent_id] = priority

    # -- Release -------------------------------------------------------------

    def release(self, agent_id: AgentId) -> None:
        """Return *agent_id*'s slot to the pool.

        Removes from active tracking and the pause set (if present).
        Decrements total so the freed slot is available for future spawns.
        """
        with self._lock:
            if agent_id in self._active:
                del self._active[agent_id]
                self._paused.discard(agent_id)
                if self._total > 1:
                    self._total -= 1

    # -- Cooperative pause check --------------------------------------------

    def is_paused(self, agent_id: AgentId) -> bool:
        """Return True if *agent_id* has been issued a cooperative pause signal.

        Agents check this before each LLM call. When True they should stop
        spawning new work and return a partial result (status="paused").
        """
        with self._lock:
            return agent_id in self._paused

    # -- Dynamic reprioritization --------------------------------------------

    def reprioritize(self, agent_id: AgentId, new_priority: Priority) -> None:
        """Change *agent_id*'s priority mid-run.

        If demoted below NORMAL and the pool is at capacity, the agent is
        automatically paused (it may be preempted to make room for others).

        If promoted and previously paused, the pause is lifted.
        """
        with self._lock:
            if agent_id not in self._active:
                return
            self._active[agent_id] = new_priority
            if new_priority < Priority.NORMAL and self._total >= self._max_agents:
                self._paused.add(agent_id)
            elif new_priority >= Priority.NORMAL:
                self._paused.discard(agent_id)

    # -- Introspection -------------------------------------------------------

    @property
    def total_spawned(self) -> int:
        """Current total agent count in the run (including root)."""
        return self._total

    def priority_of(self, agent_id: AgentId) -> Priority | None:
        """Return the current priority of *agent_id*, or None if not active."""
        with self._lock:
            return self._active.get(agent_id)

"""Supervision hierarchy — agent execution policy and tree position.

Supervision types live here rather than in ``identity`` because they model
execution policy, not routing identity.  ``AgentId`` and ``TopicId``
(in ``identity.py``) are pure routing keys; ``Supervision``, ``Priority``,
and ``HistoryRetention`` are policy metadata that flows down the agent tree.

Budget model (supervision v2)
------------------------------
Two orthogonal frozen configs are embedded in every ``Supervision`` node:

``SpawnBudget``
    Run-wide cap on the total number of agents that may be spawned.
    Shared across the entire tree — the same object is propagated to every
    child via ``spawn_child()``.  The L1 ``SpawnTracker`` (agents layer)
    carries the mutable state (current count, paused set) that enforces it.

``ExecutionBudget``
    Per-agent resource limits: tokens, cost, turns, wall-clock time.
    Each child inherits the parent's policy by default; ``spawn_child()``
    accepts an override so a sub-agent can be given a tighter budget.
    The L1 ``ExecutionTracker`` (agents layer) tracks consumption against it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

from agent_substrate.kernel.core.identity import AgentId, TopicId


class HistoryRetention(str, Enum):
    """How long a subagent's conversation history is kept after the run ends.

    NONE      — stateless worker; no history persisted at all.
    RUN       — kept for the run (scoped by run_id), deleted after.
    PERMANENT — kept forever. For top-level user-facing agents.
    """

    NONE = "none"
    RUN = "run"
    PERMANENT = "permanent"


class Priority(int, Enum):
    """Budget weight for an agent branch.

    Values are integer weights used for proportional pool allocation.
    A CRITICAL agent gets 8x the default share; BACKGROUND is best-effort.
    """

    BACKGROUND = 0
    LOW = 1
    NORMAL = 2
    HIGH = 4
    CRITICAL = 8


@dataclass(frozen=True, slots=True)
class SpawnBudget:
    """Run-wide cap on how many agents may exist simultaneously.

    Shared across the entire agent tree — every ``Supervision`` node in one
    run carries the same ``SpawnBudget`` instance so the headcount limit is
    global, not per-branch.

    ``max_agents``   — total agents allowed in the run (root counts as 1).
    ``allow_preempt``— when True, HIGH/CRITICAL agents may cooperatively
                       pause lower-priority ones to claim their slot instead
                       of raising ``SpawnDenied`` immediately.
    """

    max_agents: int = 50
    allow_preempt: bool = True


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    """Per-agent resource limits — frozen policy carried in Supervision.

    ``None`` means unlimited for that dimension.  The L1 ``ExecutionTracker``
    (agents layer) tracks actual consumption and raises ``BudgetExhaustedError``
    when a limit is breached.

    ``max_tokens``   — total LLM tokens (prompt + completion) across all turns.
    ``max_cost_usd`` — cumulative LLM cost in USD.
    ``max_turns``    — number of LLM round-trips (one tool call = one turn).
    ``deadline_s``   — wall-clock seconds from run start; enforced by RunContext.
    """

    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_turns: int | None = None
    deadline_s: float | None = None


@dataclass(frozen=True, slots=True)
class Supervision:
    """An agent's formal position in an execution hierarchy.

    Analogous to an org-chart position: who your manager is, which project
    you're on, what level you sit at, and what the org's resource limits are.
    Passed top-down when spawning child agents so every agent in the tree
    shares the same ``run_id``, ``session_id``, and resource policy.

    Two ids serve different purposes:
    - ``session_id`` — the conversation thread (long-lived; many runs).
      History is always keyed by ``session_id``.
    - ``run_id`` — one execution tree (short-lived; one run() call).
      Scopes budget, supervision, resume, and the progress pub/sub topic.

    Progress channel: ``TopicId("agent.progress", run_id)``
    All agents in one run publish there; the UI subscribes once.

    ``depth`` is informational only (for UI indentation and AgentProgress).
    There is no depth limit; ``spawn_budget`` is the single unified constraint.
    """

    run_id: str
    session_id: str
    root_id: AgentId
    parent_id: AgentId | None
    depth: int = 0
    spawn_budget: SpawnBudget = field(default_factory=SpawnBudget)
    execution_budget: ExecutionBudget = field(default_factory=ExecutionBudget)
    retention: HistoryRetention = HistoryRetention.RUN
    priority: Priority = Priority.NORMAL

    @classmethod
    def root(
        cls,
        agent_id: AgentId,
        *,
        session_id: str | None = None,
        spawn_budget: SpawnBudget | None = None,
        execution_budget: ExecutionBudget | None = None,
        retention: HistoryRetention = HistoryRetention.PERMANENT,
        priority: Priority = Priority.NORMAL,
    ) -> Supervision:
        """Create the root supervision context — generates a fresh run_id.

        Call this once on the top-level (user-facing) agent. Pass the
        returned object into every spawned child via ``spawn_child()``.

        Parameters
        ----------
        session_id:
            The conversation thread id.  If ``None``, a fresh uuid is
            generated.  Pass an explicit id to resume a multi-turn session.
        spawn_budget:
            Run-wide agent headcount policy.  Defaults to ``SpawnBudget()``
            (50 agents, preemption allowed).
        execution_budget:
            Per-agent resource limits for the root agent.  Defaults to
            ``ExecutionBudget()`` (all unlimited).
        """
        return cls(
            run_id=uuid4().hex,
            session_id=session_id or uuid4().hex,
            root_id=agent_id,
            parent_id=None,
            depth=0,
            spawn_budget=spawn_budget or SpawnBudget(),
            execution_budget=execution_budget or ExecutionBudget(),
            retention=retention,
            priority=priority,
        )

    def spawn_child(
        self,
        parent_id: AgentId,
        *,
        retention: HistoryRetention = HistoryRetention.RUN,
        priority: Priority = Priority.NORMAL,
        execution_budget: ExecutionBudget | None = None,
    ) -> Supervision:
        """Create supervision for a child agent reporting to ``parent_id``.

        Carries the same ``run_id``, ``session_id``, and ``spawn_budget``
        (the headcount cap is shared across the whole tree).  ``depth``
        increments by one.  ``execution_budget`` defaults to the parent's
        policy; pass an explicit value to give the child tighter limits.
        """
        return Supervision(
            run_id=self.run_id,
            session_id=self.session_id,
            root_id=self.root_id,
            parent_id=parent_id,
            depth=self.depth + 1,
            spawn_budget=self.spawn_budget,  # shared tree-wide
            execution_budget=execution_budget or self.execution_budget,
            retention=retention,
            priority=priority,
        )

    @property
    def progress_topic(self) -> TopicId:
        """The single pub/sub topic for all progress events in this run."""
        return TopicId("agent.progress", self.run_id)

    @property
    def is_root(self) -> bool:
        return self.parent_id is None


__all__ = [
    "HistoryRetention",
    "Priority",
    "SpawnBudget",
    "ExecutionBudget",
    "Supervision",
]

"""Supervision — an agent's position in an execution tree, and the policy it runs under.

``Actor`` and ``Topic`` are pure routing addresses. ``Supervision`` is policy
that flows down the agent tree: who the parent is, how deep this agent sits, how
many agents may exist, and what one agent may spend.

Two budgets, deliberately separate:

``SpawnBudget``
    A run-wide cap on how many agents may exist. Shared by every node in the
    tree, so the headcount is global rather than per-branch.

``ExecutionBudget``
    Resource limits for one agent: tokens, cost, turns, wall-clock time.

Both are persisted with a spawned run and read back by whichever worker later
leases it, which may be a different version of the code. These are therefore
models that ignore fields they do not know, rather than hand-written
``to_dict``/``from_dict`` pairs that crash on an unknown key and silently drop a
new one.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.ids import new_id


class HistoryRetention(StrEnum):
    """How long a subagent's conversation history is kept after the run ends.

    NONE      — stateless worker; no history persisted at all.
    RUN       — kept for the run, deleted after.
    PERMANENT — kept forever. For top-level user-facing agents.
    """

    NONE = "none"
    RUN = "run"
    PERMANENT = "permanent"


class Priority(IntEnum):
    """Queue priority: higher ranks are leased first when work exceeds capacity."""

    BACKGROUND = 0
    LOW = 1
    NORMAL = 2
    HIGH = 4
    CRITICAL = 8


class _Policy(BaseModel):
    """Persisted policy: frozen, and tolerant of fields from another version."""

    model_config = ConfigDict(frozen=True, extra="ignore", arbitrary_types_allowed=True)


class SpawnBudget(_Policy):
    """Run-wide cap on how many agents may exist at once (the root counts as 1)."""

    max_agents: int = Field(default=50, ge=1)


class ExecutionBudget(_Policy):
    """Resource limits for one agent. ``None`` means unlimited for that dimension.

    ``max_tokens``   — total LLM tokens (prompt + completion) across all turns.
    ``max_cost_usd`` — cumulative LLM cost in USD.
    ``max_turns``    — number of LLM round-trips.
    ``deadline_s``   — wall-clock seconds from the run's start.
    """

    max_tokens: int | None = Field(default=None, ge=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_turns: int | None = Field(default=None, ge=0)
    deadline_s: float | None = Field(default=None, ge=0)


class Supervision(_Policy):
    """An agent's formal position in an execution hierarchy.

    Two ids with different lifetimes:

    - ``session_id`` — the conversation thread (long-lived; many runs). History
      is keyed by it.
    - ``run_id`` — the *execution tree* (short-lived; one root run). Scopes the
      spawn budget and supervision. Note that it names the whole tree: each
      spawned child is its own durable run with its own run id, and carries this
      one as the tree it belongs to.

    ``depth`` is informational (UI indentation); ``spawn_budget`` is the only
    constraint on how large the tree may grow.
    """

    run_id: str
    session_id: str
    root_id: Actor
    parent_id: Actor | None = None
    depth: int = Field(default=0, ge=0)
    spawn_budget: SpawnBudget = Field(default_factory=SpawnBudget)
    execution_budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
    retention: HistoryRetention = HistoryRetention.RUN
    priority: Priority = Priority.NORMAL

    @classmethod
    def root(
        cls,
        agent_id: Actor,
        *,
        session_id: str | None = None,
        spawn_budget: SpawnBudget | None = None,
        execution_budget: ExecutionBudget | None = None,
        retention: HistoryRetention = HistoryRetention.PERMANENT,
        priority: Priority = Priority.NORMAL,
    ) -> Supervision:
        """The supervision of a top-level agent: a fresh tree, no parent."""
        return cls(
            run_id=new_id(),
            session_id=session_id or new_id(),
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
        parent_id: Actor,
        *,
        retention: HistoryRetention = HistoryRetention.RUN,
        priority: Priority = Priority.NORMAL,
        execution_budget: ExecutionBudget | None = None,
    ) -> Supervision:
        """Supervision for a child reporting to ``parent_id``.

        Same tree, same conversation, same shared spawn budget; one level deeper.
        The execution budget defaults to the parent's; pass one to give the child
        tighter limits.
        """
        return self.model_copy(
            update={
                "parent_id": parent_id,
                "depth": self.depth + 1,
                "execution_budget": execution_budget or self.execution_budget,
                "retention": retention,
                "priority": priority,
            }
        )

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for persisting alongside a spawned run."""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Supervision:
        return cls.model_validate(data)


__all__ = [
    "ExecutionBudget",
    "HistoryRetention",
    "Priority",
    "SpawnBudget",
    "Supervision",
]

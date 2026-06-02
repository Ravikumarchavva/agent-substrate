"""Agent and topic routing identities, plus the supervision hierarchy contract."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class AgentId:
    """Stable routing key for a logical agent instance.

    ``type`` is the agent role name; ``key`` uniquely identifies the instance
    within that type (e.g. a session ID or a generated UUID).
    """

    type: str
    key: str

    def __str__(self) -> str:
        return f"{self.type}/{self.key}"

    @classmethod
    def generate(cls, agent_type: str) -> AgentId:
        """Create an AgentId with a random key."""
        return cls(type=agent_type, key=uuid.uuid4().hex)


@dataclass(frozen=True, slots=True)
class TopicId:
    """Routing key for a pub/sub topic.

    ``type`` identifies the topic category; ``source`` scopes it to a
    particular origin (e.g. a run_id, a session, or a pipeline).

    Standard topic conventions:
        agent.progress / <run_id>   — all progress events for one execution run
        agent.stream   / <run_id>   — token stream for a specific run
    """

    type: str
    source: str = "default"

    def __str__(self) -> str:
        return f"{self.type}/{self.source}"


# ---------------------------------------------------------------------------
# Supervision hierarchy
# ---------------------------------------------------------------------------


class HistoryRetention(str, Enum):
    """How long a subagent's conversation history is kept after the run ends.

    NONE      — stateless worker; no history persisted at all.
    RUN       — kept for the run (scoped by run_id), deleted after. Default for subagents.
    PERMANENT — kept forever. For top-level user-facing agents.
    """

    NONE = "none"
    RUN = "run"
    PERMANENT = "permanent"


class Priority(int, Enum):
    """Budget weight for an agent branch.

    Values are integer weights used for proportional pool allocation.
    A CRITICAL agent gets 8x the default share; BACKGROUND is best-effort.

    Real-org model: product team (CRITICAL/HIGH) gets most headcount;
    pro-bono (LOW/BACKGROUND) gets what's left over. Priority weights spend
    indirectly — a preempted low-priority agent is paused and stops calling
    the LLM, so it stops spending.
    """

    BACKGROUND = 0  # best-effort; first to be paused, last to get slots
    LOW = 1         # below-normal; e.g. cleanup, logging
    NORMAL = 2      # standard work (default)
    HIGH = 4        # preempts NORMAL and below when pool is full
    CRITICAL = 8    # P0 escalation; preempts everything below


@dataclass(frozen=True, slots=True)
class Supervision:
    """An agent's formal position in an execution hierarchy.

    Analogous to an org-chart position: who your manager is, which project
    you're on, what level you sit at, and what the org's resource limits are.
    Passed top-down when spawning child agents so every agent in the tree
    shares the same ``run_id``, ``session_id``, and resource policy.

    Two ids serve different purposes:
    - ``session_id`` — the conversation thread (long-lived; many runs).
      History is always keyed by ``session_id``.  Subagents with
      ``HistoryRetention.PERMANENT`` accumulate memory across runs in
      the same session.
    - ``run_id`` — one execution tree (short-lived; scopes budget,
      supervision, resume, and the progress pub/sub topic).

    Progress channel: ``TopicId("agent.progress", run_id)``
    All agents in one run publish there; the UI subscribes once to see everything.

    Resource policy (enforced by the agents layer, not this dataclass):
    ┌─────────────────┬────────────────────────────────────────────────────────┐
    │ max_agents      │ headcount cap — total agents across the whole run tree  │
    └─────────────────┴────────────────────────────────────────────────────────┘
    ``Supervision`` holds the policy numbers. The mutable ``SpawnBudget``
    (agents layer) tracks actual counts and raises when limits are hit.

    ``depth`` is informational only (used for UI indentation and AgentProgress).
    There is no depth limit; the budget is the single unified constraint.
    """

    run_id: str                              # unique per execution tree
    session_id: str                          # conversation thread; history scope
    root_id: AgentId                         # top of the tree
    parent_id: AgentId | None                # direct manager; None = this IS the root
    depth: int = 0                           # 0=root, 1=direct report, 2=… (informational)
    max_agents: int = 50                     # total headcount cap for the whole run
    retention: HistoryRetention = HistoryRetention.RUN
    priority: Priority = Priority.NORMAL     # budget weight for this branch

    @classmethod
    def root(
        cls,
        agent_id: AgentId,
        *,
        session_id: str | None = None,
        max_agents: int = 50,
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
            generated (one-shot / standalone use).  Pass an explicit id
            to resume or continue a multi-turn conversation.
        """
        return cls(
            run_id=uuid4().hex,
            session_id=session_id or uuid4().hex,
            root_id=agent_id,
            parent_id=None,
            depth=0,
            max_agents=max_agents,
            retention=retention,
            priority=priority,
        )

    def spawn_child(
        self,
        parent_id: AgentId,
        *,
        retention: HistoryRetention = HistoryRetention.RUN,
        priority: Priority = Priority.NORMAL,
    ) -> Supervision:
        """Create supervision for a child agent reporting to ``parent_id``.

        Carries the same ``run_id`` and ``session_id``, increments ``depth``,
        and propagates ``max_agents`` so the entire subtree inherits the same
        constraint.

        No depth limit is raised here — ``SpawnBudget`` enforces headcount.
        The child's priority can differ from the parent's (e.g. spawn a
        CRITICAL subagent from a NORMAL orchestrator).
        """
        return Supervision(
            run_id=self.run_id,
            session_id=self.session_id,
            root_id=self.root_id,
            parent_id=parent_id,
            depth=self.depth + 1,
            max_agents=self.max_agents,
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


__all__ = ["AgentId", "TopicId", "HistoryRetention", "Priority", "Supervision"]

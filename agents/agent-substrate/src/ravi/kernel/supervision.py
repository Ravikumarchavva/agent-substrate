"""Supervision hierarchy — agent execution policy and tree position.

Supervision types live here rather than in ``identity`` because they model
execution policy, not routing identity.  ``AgentId`` and ``TopicId``
(in ``identity.py``) are pure routing keys; ``Supervision``, ``Priority``,
and ``HistoryRetention`` are policy metadata that flows down the agent tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

from ravi.kernel.identity import AgentId, TopicId


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
    There is no depth limit; the budget is the single unified constraint.
    """

    run_id: str
    session_id: str
    root_id: AgentId
    parent_id: AgentId | None
    depth: int = 0
    max_agents: int = 50
    retention: HistoryRetention = HistoryRetention.RUN
    priority: Priority = Priority.NORMAL

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
            generated.  Pass an explicit id to resume a multi-turn session.
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
        and propagates ``max_agents`` so the entire subtree inherits the limit.
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


__all__ = ["HistoryRetention", "Priority", "Supervision"]

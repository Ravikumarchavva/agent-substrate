"""Runtime errors — single source of truth for agent execution failures."""

from __future__ import annotations

from ravi.kernel.identity import AgentId


class AgentNotFoundError(Exception):
    """Raised when sending to an AgentId that has no registered handler."""


class HandlerError(Exception):
    """Raised when a message handler raises an exception.

    Wraps the original exception so callers of ``send_message`` receive a
    typed error rather than a bare exception or silent ``None``.
    """


class AgentCrashError(Exception):
    """Raised when an agent's run fails with an unexpected exception.

    Unlike ``HandlerError`` (transport-level), ``AgentCrashError`` is a
    semantic failure — the agent encountered an unrecoverable error during
    its ReAct loop. The orchestrator can catch this, consult ``RetryPolicy``,
    and call ``agent.run(input_text, resume=True)`` to restart from the
    persisted history checkpoint.

    ``run_id`` and ``agent_id`` identify which run/agent failed so the
    orchestrator knows which history to reload on resume.
    """

    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        agent_id: AgentId,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.agent_id = agent_id


class BudgetExhaustedError(Exception):
    """Raised when the total agent headcount for a run reaches max_agents.

    Prevents runaway trees where many levels each spawn many children,
    multiplying to thousands of agents (and LLM calls) in one run.

    Also raised when an agent's per-agent ``ExecutionBudget`` is exceeded
    (token/cost/turn cap).
    """


__all__ = [
    "AgentNotFoundError",
    "HandlerError",
    "AgentCrashError",
    "BudgetExhaustedError",
]

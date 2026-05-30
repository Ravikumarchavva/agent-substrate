from __future__ import annotations

from typing import Protocol

from ravi.kernel import AgentId


class FailurePolicy(Protocol):
    """Determines recovery action when a supervised agent crashes."""

    async def handle_failure(self, agent_id: AgentId, exception: Exception) -> bool:
        """Handle the failure.

        Returns ``True`` if the agent should be restarted, ``False`` to
        escalate to the parent supervisor.
        """
        ...


class RetryPolicy:
    """Restarts the agent up to *max_retries* times, then escalates."""

    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries
        self._counts: dict[AgentId, int] = {}

    async def handle_failure(self, agent_id: AgentId, exception: Exception) -> bool:
        count = self._counts.get(agent_id, 0)
        if count < self.max_retries:
            self._counts[agent_id] = count + 1
            return True
        return False

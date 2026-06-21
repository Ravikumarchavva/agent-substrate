from __future__ import annotations

from substrate.kernel import AgentId


class RetryPolicy:
    """Restarts a failed agent up to *max_retries* times, then gives up."""

    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries
        self._counts: dict[AgentId, int] = {}

    def should_retry(self, agent_id: AgentId) -> bool:
        count = self._counts.get(agent_id, 0)
        if count < self.max_retries:
            self._counts[agent_id] = count + 1
            return True
        return False

    def reset(self, agent_id: AgentId) -> None:
        self._counts.pop(agent_id, None)

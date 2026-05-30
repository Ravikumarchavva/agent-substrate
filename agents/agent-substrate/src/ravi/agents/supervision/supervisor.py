from __future__ import annotations

from typing import Protocol

from ravi.kernel import AgentId
from .policies import FailurePolicy


class Supervisor(Protocol):
    """OTP-style supervisor that monitors and restarts agents on failure."""

    @property
    def supervised_agents(self) -> list[AgentId]: ...

    async def watch(self, agent_id: AgentId, policy: FailurePolicy) -> None:
        """Register *agent_id* to be monitored under *policy*."""
        ...

    async def unwatch(self, agent_id: AgentId) -> None:
        """Stop monitoring *agent_id*."""
        ...

    async def handle_crash(self, agent_id: AgentId, exception: Exception) -> None:
        """Triggered when a supervised agent crashes; applies the policy."""
        ...

from __future__ import annotations

from typing import Any, Protocol

from ravi.kernel import AgentId


class Spawner(Protocol):
    """Dynamic agent instantiation by agent type name."""

    async def spawn(
        self,
        agent_type: str,
        initial_context: dict[str, Any],
        parent_id: AgentId,
    ) -> AgentId:
        """Instantiate a new agent of *agent_type* and return its ID."""
        ...

from __future__ import annotations

from typing import Any, Protocol

from ravi.kernel import AgentId
from ravi.fabric.catalog import Namespace


class Spawner(Protocol):
    """Dynamic agent instantiation from a catalog blueprint.

    Allows an agent to spawn fully-empowered sub-agents at runtime.
    """

    async def spawn(
        self,
        blueprint_namespace: Namespace,
        initial_context: dict[str, Any],
        parent_id: AgentId,
    ) -> AgentId:
        """Instantiate a new agent from *blueprint_namespace* and return its ID."""
        ...

from __future__ import annotations

from typing import Protocol

from ravi.kernel import AgentId
from .namespace import Capability, Namespace


class CapabilityRegistry(Protocol):
    """Central catalog for discovering and binding agent capabilities.

    Agents query this registry to find tools or blueprints they are
    authorized to use.  Implementations enforce ACLs per agent identity.
    """

    async def register(self, capability: Capability) -> None:
        """Register a new capability."""
        ...

    async def unregister(self, namespace: Namespace) -> None:
        """Remove a capability."""
        ...

    async def search(
        self,
        query: str,
        agent_id: AgentId,
        namespace_prefix: str | None = None,
    ) -> list[Capability]:
        """Search for capabilities authorized for *agent_id*.

        ``namespace_prefix`` is an optional glob (e.g. ``"finance.*"``).
        """
        ...

    async def get(self, namespace: Namespace, agent_id: AgentId) -> Capability:
        """Return a specific capability, enforcing ACLs.

        Raises ``PermissionError`` when the agent is not authorized.
        """
        ...

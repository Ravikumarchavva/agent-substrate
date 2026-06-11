"""Agent and topic routing identities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentId:
    """Stable routing key for a logical agent instance.

    ``type`` is the agent role name; ``key`` uniquely identifies the instance
    within that type (e.g. a session ID or a generated UUID).

    ``namespace`` scopes agents in a multi-tenant deployment so that agents
    in different tenants with the same type and key do not collide on shared
    routing infrastructure (e.g. Redis pub/sub, NATS subjects).
    """

    type: str
    key: str
    namespace: str = "default"

    def __str__(self) -> str:
        if self.namespace == "default":
            return f"{self.type}/{self.key}"
        return f"{self.namespace}/{self.type}/{self.key}"

    @classmethod
    def generate(cls, agent_type: str, *, namespace: str = "default") -> AgentId:
        """Create an AgentId with a random key."""
        return cls(type=agent_type, key=uuid.uuid4().hex, namespace=namespace)


@dataclass(frozen=True, slots=True)
class TopicId:
    """Routing key for a pub/sub topic.

    ``type`` identifies the topic category; ``source`` scopes it to a
    particular origin (e.g. a run_id, a session, or a pipeline).

    ``namespace`` scopes topics in a multi-tenant deployment to prevent
    cross-tenant event bleed on shared message brokers.

    Standard topic conventions:
        agent.progress / <run_id>   — all progress events for one execution run
        agent.stream   / <run_id>   — token stream for a specific run
    """

    type: str
    source: str = "default"
    namespace: str = "default"

    def __str__(self) -> str:
        if self.namespace == "default":
            return f"{self.type}/{self.source}"
        return f"{self.namespace}/{self.type}/{self.source}"


__all__ = ["AgentId", "TopicId"]

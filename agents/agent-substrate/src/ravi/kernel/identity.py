"""Agent and topic routing identities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentId:
    """Stable routing key for a logical agent instance.

    ``type`` is the agent class name; ``key`` uniquely identifies the instance
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
    particular origin (e.g. a session, a pipeline, or a user ID).
    """

    type: str
    source: str = "default"

    def __str__(self) -> str:
        return f"{self.type}/{self.source}"


__all__ = ["AgentId", "TopicId"]

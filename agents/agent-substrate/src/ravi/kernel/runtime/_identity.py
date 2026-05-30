"""Routing keys for the agent runtime."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentId:
    """Routing key for a logical agent — stable, durable."""

    type: str
    key: str

    def __str__(self) -> str:
        return f"{self.type}/{self.key}"

    @classmethod
    def generate(cls, agent_type: str) -> AgentId:
        return cls(type=agent_type, key=uuid.uuid4().hex)


@dataclass(frozen=True, slots=True)
class TopicId:
    """Routing key for a pub/sub topic."""

    type: str
    source: str

    def __str__(self) -> str:
        return f"{self.type}/{self.source}"


__all__ = ["AgentId", "TopicId"]

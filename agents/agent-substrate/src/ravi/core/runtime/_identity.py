"""Identity value-objects for the agent runtime.

``AgentId`` and ``TopicId`` are frozen, hashable dataclasses used as
dictionary keys throughout the runtime.  They enforce a strict character
set via regex validation on construction.

All types are pure Python — no external dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Agent type / key must be alphanumeric, underscores, hyphens, or dots.
_VALID_ID_RE = re.compile(r"^[\w\-.]+\Z")


@dataclass(frozen=True, slots=True)
class AgentId:
    """Uniquely identifies an agent instance.

    ``type`` is the agent class/kind (e.g. ``"react_agent"``).
    ``key`` scopes the instance (e.g. a thread or session id).
    """

    type: str
    key: str

    def __post_init__(self) -> None:
        if not _VALID_ID_RE.match(self.type):
            raise ValueError(
                f"Invalid agent type: {self.type!r}. "
                r"Must match [\w\-.]+."
            )
        if not _VALID_ID_RE.match(self.key):
            raise ValueError(
                f"Invalid agent key: {self.key!r}. "
                r"Must match [\w\-.]+."
            )

    def __str__(self) -> str:
        return f"{self.type}/{self.key}"


@dataclass(frozen=True, slots=True)
class TopicId:
    """Identifies a pub/sub topic.

    ``type`` is the event category (e.g. ``"sse_events"``).
    ``source`` scopes the topic (e.g. a thread id).
    """

    type: str
    source: str

    def __post_init__(self) -> None:
        if not _VALID_ID_RE.match(self.type):
            raise ValueError(
                f"Invalid topic type: {self.type!r}. "
                r"Must match [\w\-.]+."
            )
        if not _VALID_ID_RE.match(self.source):
            raise ValueError(
                f"Invalid topic source: {self.source!r}. "
                r"Must match [\w\-.]+."
            )

    def __str__(self) -> str:
        return f"{self.type}/{self.source}"

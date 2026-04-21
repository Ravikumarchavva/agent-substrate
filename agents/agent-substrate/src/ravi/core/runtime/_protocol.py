"""Runtime protocol and identity types.

Defines the ``AgentRuntime`` protocol that every runtime backend must
implement, plus the value-objects used to identify agents and topics.

All types are pure Python — no external dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Awaitable, Protocol, runtime_checkable

# Agent type / key must be alphanumeric, underscores, hyphens, or dots.
_VALID_ID_RE = re.compile(r"^[\w\-.]+\Z")


# ---------------------------------------------------------------------------
# Identity value-objects (frozen, hashable, usable as dict keys)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AgentFactory — callable that creates a message handler for an agent
# ---------------------------------------------------------------------------

AgentFactory = Callable[..., Awaitable[Any]]


# ---------------------------------------------------------------------------
# AgentRuntime protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentRuntime(Protocol):
    """Contract that all runtime backends implement.

    ``LocalRuntime`` (in-process, asyncio-only) ships with the framework.
    Production backends (gRPC, Restate, NATS) implement this same protocol
    in ``integrations/runtime/``.
    """

    async def send_message(
        self,
        message: Any,
        *,
        sender: AgentId | None = None,
        recipient: AgentId,
    ) -> Any:
        """Point-to-point: deliver *message* to *recipient* and return its response."""
        ...

    async def publish_message(
        self,
        message: Any,
        *,
        sender: AgentId | None = None,
        topic: TopicId,
    ) -> None:
        """Pub/sub: broadcast *message* to all subscribers of *topic*."""
        ...

    async def register(
        self,
        agent_type: str,
        factory: AgentFactory,
    ) -> None:
        """Register an agent type with its factory.

        The runtime may instantiate agents lazily (on first message) or
        eagerly — that is an implementation detail.
        """
        ...

    async def subscribe(
        self,
        agent_type: str,
        topic: TopicId,
    ) -> None:
        """Bind *agent_type* so that all instances receive messages on *topic*."""
        ...

    async def start(self) -> None:
        """Start the runtime (connect transports, spin up workers, etc.)."""
        ...

    async def stop(self) -> None:
        """Gracefully shut down the runtime."""
        ...

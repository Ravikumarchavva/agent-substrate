"""BaseRuntime — abstract base class for all ``AgentRuntime`` implementations.

Provides the shared handler registry, topic-binding management, and
introspection properties that every runtime backend needs.  Concrete
subclasses implement the messaging primitives (``send_message``,
``publish_message``) and lifecycle methods (``start``, ``stop``).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from ravi.core.runtime._identity import AgentId, TopicId
from ravi.core.runtime._contracts import MessageHandler

logger = logging.getLogger(__name__)


class BaseRuntime(ABC):
    """Abstract base for all ``AgentRuntime`` implementations.

    Handles agent-type registration and topic-subscription management.
    Subclasses inherit this state and override the abstract methods
    to provide specific transport/dispatch behaviour.
    """

    __slots__ = (
        "_handlers",
        "_topic_bindings",
        "_started",
    )

    def __init__(self) -> None:
        self._handlers: dict[str, MessageHandler] = {}
        self._topic_bindings: list[tuple[str, TopicId]] = []
        self._started: bool = False

    # -- Shared concrete methods --------------------------------------------

    async def register(
        self,
        agent_type: str,
        handler: MessageHandler,
    ) -> None:
        """Register an agent type and its message handler.

        Subclasses may override to add lifecycle guards or validation.
        """
        self._handlers[agent_type] = handler
        logger.debug("registered agent type %r", agent_type)

    async def subscribe(
        self,
        agent_type: str,
        topic: TopicId,
    ) -> None:
        """Bind *agent_type* to a topic so instances receive its messages."""
        if agent_type not in self._handlers:
            raise ValueError(f"unknown agent type: {agent_type!r}")
        self._topic_bindings.append((agent_type, topic))

    # -- Introspection ------------------------------------------------------

    @property
    def registered_types(self) -> list[str]:
        """Agent types currently registered with this runtime."""
        return list(self._handlers.keys())

    # -- Abstract interface -------------------------------------------------

    @abstractmethod
    async def send_message(
        self,
        message: object,
        *,
        sender: AgentId | None = None,
        recipient: AgentId,
    ) -> object: ...

    @abstractmethod
    async def publish_message(
        self,
        message: object,
        *,
        sender: AgentId | None = None,
        topic: TopicId,
    ) -> None: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

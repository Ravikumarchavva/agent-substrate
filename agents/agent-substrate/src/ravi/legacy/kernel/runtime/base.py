"""BaseRuntime — abstract base class for all ``AgentRuntime`` implementations.

Provides the shared handler registry, topic-binding management, optional
lease coordination, and introspection properties that every runtime backend
needs. Concrete subclasses implement the messaging primitives
(``send_message``, ``publish_message``) and lifecycle methods
(``start``, ``stop``).

Thread-safety
~~~~~~~~~~~~~
Handler registration and topic bindings may be mutated by routes /
control-plane callbacks while the dispatch path concurrently reads them.
A ``threading.RLock`` guards the shared state so the runtime is safe to
use under Python 3.14 free-threaded execution.
"""

from __future__ import annotations

import logging
import threading
import uuid
from abc import ABC, abstractmethod
from typing import Optional

from ravi.kernel.runtime._contracts import Envelope, MessageHandler
from ravi.kernel.runtime._identity import AgentId, TopicId
from ravi.kernel.runtime._lease import LeaseRegistry
from ravi.kernel.runtime._middleware import (
    DropEnvelope,
    RoutingMiddleware,
    RoutingMiddlewareRejection,
)

logger = logging.getLogger(__name__)


class BaseRuntime(ABC):
    """Abstract base for all ``AgentRuntime`` implementations.

    Parameters
    ----------
    lease_registry:
        Optional :class:`LeaseRegistry` used to coordinate exclusive
        agent activation across workers. When ``None``, the runtime
        operates in single-worker mode (no lease coordination).
    worker_id:
        Stable identifier for this worker. Carried on every
        ``ExecutionLease`` so contention diagnostics name a real owner.
        Defaults to a per-process random hex string.
    """

    __slots__ = (
        "_handlers",
        "_topic_bindings",
        "_started",
        "_lock",
        "_lease_registry",
        "_worker_id",
        "_routing_middleware",
    )

    def __init__(
        self,
        *,
        lease_registry: Optional[LeaseRegistry] = None,
        worker_id: Optional[str] = None,
        routing_middleware: Optional[list[RoutingMiddleware]] = None,
    ) -> None:
        self._handlers: dict[str, MessageHandler] = {}
        self._topic_bindings: list[tuple[str, TopicId]] = []
        self._started: bool = False
        self._lock = threading.RLock()
        self._lease_registry: Optional[LeaseRegistry] = lease_registry
        self._worker_id: str = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self._routing_middleware: list[RoutingMiddleware] = list(
            routing_middleware or []
        )

    # -- Shared concrete methods --------------------------------------------

    async def register(
        self,
        agent_type: str,
        handler: MessageHandler,
    ) -> None:
        """Register an agent type and its message handler.

        Subclasses may override to add lifecycle guards or validation.
        """
        with self._lock:
            self._handlers[agent_type] = handler
        logger.debug("registered agent type %r", agent_type)

    async def subscribe(
        self,
        agent_type: str,
        topic: TopicId,
    ) -> None:
        """Bind *agent_type* to a topic so instances receive its messages.

        Idempotent: repeated calls with the same ``(agent_type, topic)`` are
        no-ops. This prevents duplicate fan-out on a single publish.
        """
        with self._lock:
            if agent_type not in self._handlers:
                raise ValueError(f"unknown agent type: {agent_type!r}")
            binding = (agent_type, topic)
            if binding in self._topic_bindings:
                return
            self._topic_bindings.append(binding)

    # -- Introspection ------------------------------------------------------

    @property
    def registered_types(self) -> list[str]:
        """Agent types currently registered with this runtime."""
        with self._lock:
            return list(self._handlers.keys())

    @property
    def worker_id(self) -> str:
        """Stable identifier for this worker (carried on every lease)."""
        return self._worker_id

    @property
    def lease_registry(self) -> Optional[LeaseRegistry]:
        """The configured lease registry, or ``None`` for single-worker mode."""
        return self._lease_registry

    @property
    def routing_middleware(self) -> list[RoutingMiddleware]:
        """Snapshot of the configured routing middleware chain (in order)."""
        with self._lock:
            return list(self._routing_middleware)

    def add_routing_middleware(self, middleware: RoutingMiddleware) -> None:
        """Append ``middleware`` to the chain (runs after existing entries)."""
        with self._lock:
            self._routing_middleware.append(middleware)

    async def _apply_routing_middleware(self, envelope: Envelope) -> bool:
        """Run the chain. Return ``True`` to allow dispatch, ``False`` to drop.

        Re-raises :class:`RoutingMiddlewareRejection` so the caller can surface
        it. ``DropEnvelope`` is swallowed silently and yields ``False``.
        """
        with self._lock:
            chain = list(self._routing_middleware)
        for mw in chain:
            try:
                await mw(envelope)
            except DropEnvelope as drop:
                logger.info(
                    "envelope %s dropped by %s: %s",
                    envelope.correlation_id,
                    drop.policy_name,
                    drop.reason,
                )
                return False
            except RoutingMiddlewareRejection:
                raise
        return True

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

    async def __aenter__(self) -> "BaseRuntime":
        """Start the runtime and return self for use as a context manager."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Stop the runtime on context manager exit."""
        await self.stop()

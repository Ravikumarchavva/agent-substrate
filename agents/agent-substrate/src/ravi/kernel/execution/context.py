"""Shared execution context used across agent and workflow layers.

This is intentionally small: it captures only the fields that are common
across different execution surfaces. Layer-specific contexts such as
``MiddlewareContext`` and ``WorkflowMiddlewareContext`` inherit from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ravi.kernel.messages.content import JsonObject


@dataclass
class ExecutionContext:
    """Base execution context shared by agent and workflow middleware.

    ``parent_context`` enables parent-child lineage when a workflow triggers
    child agent execution. ``metadata`` is copied forward by helpers so
    downstream middleware sees the accumulated execution state.
    """

    run_id: str = ""
    correlation_id: str = ""
    thread_id: str = ""
    input_text: str = ""
    metadata: JsonObject = field(default_factory=dict)
    parent_context: Optional["ExecutionContext"] = None

    # Agent hierarchy — enables depth-limited sub-agent delegation
    agent_id: str = ""
    parent_agent_id: str = ""
    agent_depth: int = 0
    max_agent_depth: int = 5  # hard ceiling prevents infinite recursion

    # Lifecycle — used by long-running ReAct loops
    cancelled: bool = False
    deadline: Optional[float] = (
        None  # monotonic time.monotonic() value; None = no deadline
    )

    @property
    def root_context(self) -> "ExecutionContext":
        """Return the earliest context in the parent chain."""
        current: ExecutionContext = self
        while current.parent_context is not None:
            current = current.parent_context
        return current

    def inherited_metadata(self, extra: Optional[JsonObject] = None) -> JsonObject:
        """Return parent metadata merged with this context's metadata."""
        merged: JsonObject = {}
        if self.parent_context is not None:
            merged.update(self.parent_context.inherited_metadata())
        merged.update(self.metadata)
        if extra:
            merged.update(extra)
        return merged

    def cancel(self) -> None:
        """Signal cancellation to the agent loop."""
        self.cancelled = True

    @property
    def is_cancelled(self) -> bool:
        """Return True if cancel() has been called on this context."""
        return self.cancelled

    @property
    def is_expired(self) -> bool:
        """Return True if the deadline has passed."""
        if self.deadline is None:
            return False
        import time

        return time.monotonic() > self.deadline

    @property
    def is_alive(self) -> bool:
        """Return True if the execution should continue (not cancelled and not expired)."""
        return not self.is_cancelled and not self.is_expired

    def with_deadline(self, timeout_seconds: float) -> "ExecutionContext":
        """Return a copy of this context with a deadline set *timeout_seconds* from now."""
        import time
        from dataclasses import replace

        return replace(self, deadline=time.monotonic() + timeout_seconds)

    def child_context(
        self,
        child_agent_id: str,
        *,
        metadata: Optional[JsonObject] = None,
    ) -> "ExecutionContext":
        """Create a child context for a sub-agent delegation.

        Raises ``MaxAgentDepthError`` if the depth ceiling would be exceeded.
        The child inherits run_id, correlation_id, thread_id, deadline, and
        accumulated metadata from this context.
        """
        from ravi.kernel.execution.errors import MaxAgentDepthError

        if self.agent_depth >= self.max_agent_depth:
            raise MaxAgentDepthError(
                f"Max agent depth {self.max_agent_depth} reached at agent "
                f"{self.agent_id!r}. Cannot delegate to {child_agent_id!r}."
            )
        from dataclasses import replace

        return replace(
            self,
            agent_id=child_agent_id,
            parent_agent_id=self.agent_id,
            agent_depth=self.agent_depth + 1,
            cancelled=False,  # child starts fresh
            metadata={**self.metadata, **(metadata or {})},
            parent_context=self,
        )

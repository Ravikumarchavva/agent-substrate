"""Shared execution context used across agent and workflow layers.

This is intentionally small: it captures only the fields that are common
across different execution surfaces. Layer-specific contexts such as
``MiddlewareContext`` and ``WorkflowMiddlewareContext`` inherit from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


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
    metadata: Dict[str, Any] = field(default_factory=dict)
    parent_context: Optional["ExecutionContext"] = None

    @property
    def root_context(self) -> "ExecutionContext":
        """Return the earliest context in the parent chain."""
        current: ExecutionContext = self
        while current.parent_context is not None:
            current = current.parent_context
        return current

    def inherited_metadata(
        self, extra: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Return parent metadata merged with this context's metadata."""
        merged: Dict[str, Any] = {}
        if self.parent_context is not None:
            merged.update(self.parent_context.inherited_metadata())
        merged.update(self.metadata)
        if extra:
            merged.update(extra)
        return merged

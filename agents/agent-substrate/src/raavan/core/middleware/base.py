"""Base middleware contract and context.

Middleware sits between the agent and the LLM / tool execution layer.
Unlike hooks (fire-and-forget observers) and guardrails (binary pass/fail
gates), middleware can **transform data**, **retry on failure**, or
**short-circuit** execution.

Lifecycle per agent step::

    for mw in middleware:          # forward order
        ctx = await mw.before(ctx)

    result = await execute(ctx)    # actual LLM call or tool run

    for mw in reversed(middleware): # reverse order
        result = await mw.after(ctx, result)

Errors during ``execute()`` trigger ``on_error()`` on every middleware
(reverse order), then re-raise unless a middleware suppresses the error
by returning a fallback result.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from raavan.core.execution.context import ExecutionContext


class MiddlewareStage(str, Enum):
    """Which part of the agent cycle the middleware is intercepting."""

    LLM_CALL = "llm_call"
    TOOL_EXECUTION = "tool_execution"


@dataclass
class MiddlewareContext(ExecutionContext):
    """Mutable context bag passed through the middleware chain.

    Middleware can read **and write** any field.  Downstream middleware and
    the executor see the modified values.

    Attributes:
        stage: Whether this is an LLM call or tool execution.
        agent_name: Name of the running agent.
        run_id: Unique ID for this agent run.
        input_text: The original user input.
        response_schema: Optional Pydantic model for structured output.
        tool_name: The tool being called (only for TOOL_EXECUTION stage).
        tool_args: Arguments for the tool (only for TOOL_EXECUTION stage).
        metadata: Free-form dict for middleware to share state across
            the before → execute → after chain.
    """

    stage: MiddlewareStage = MiddlewareStage.LLM_CALL
    agent_name: str = ""
    response_schema: Optional[type] = None
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseMiddleware(ABC):
    """Abstract base for all agent middleware.

    Subclasses **must** implement at least ``before`` and ``after``.
    ``on_error`` is optional — the default re-raises the exception.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        """Pre-processing hook.  Return (possibly modified) context."""
        ...

    @abstractmethod
    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        """Post-processing hook.  Return (possibly modified) result."""
        ...

    async def on_error(self, ctx: MiddlewareContext, error: Exception) -> Optional[Any]:
        """Called when the executor raises.

        Return ``None`` to let the exception propagate.
        Return a non-``None`` value to suppress the error and use that
        value as the result instead (short-circuit).
        """
        return None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"

"""Middleware contracts — one generic interceptor protocol with per-level context protocols.

Middleware[CtxT]   generic interceptor (all three levels share this shape)

Level-specific context protocols (minimal — only the fields each middleware level reads):

    AgentRunContextProtocol  → wraps one agent.run() call
    ChatContextProtocol      → wraps each model.generate() call
    FunctionContextProtocol  → wraps each tool.execute() call

These context protocols live here (not in agents/) so that the kernel can
type the middleware pipelines without knowing about concrete context classes.
The agents layer narrows ``CtxT`` to its concrete dataclasses when wiring
pipelines.

Raise ``MiddlewareTermination`` (see ``agent_substrate.kernel.errors``) from any
``process`` implementation to halt execution cleanly at that level.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, TypeVar

CtxT = TypeVar("CtxT")


class Middleware(Protocol[CtxT]):
    """Generic interceptor — call_next() continues down the chain.

    All three middleware levels (agent-run, chat, tool) share this shape.
    The concrete context type is narrowed by the pipeline at the agents layer.
    """

    async def process(
        self,
        context: CtxT,
        call_next: Callable[[], Awaitable[None]],
    ) -> None: ...


# ---------------------------------------------------------------------------
# Minimal per-level context protocols
# ---------------------------------------------------------------------------
# These describe only the attributes each middleware level actually reads so
# that middleware can be type-checked without importing concrete dataclasses.


class AgentRunContextProtocol(Protocol):
    """Attributes readable by AgentMiddleware from an agent run context."""

    agent_name: str
    run_id: str
    session_id: str


class ChatContextProtocol(Protocol):
    """Attributes readable by ChatMiddleware from a chat generation context."""

    agent_name: str
    run_id: str
    system_instructions: str


class FunctionContextProtocol(Protocol):
    """Attributes readable by FunctionMiddleware from a tool execution context."""

    agent_name: str
    run_id: str
    function_name: str
    arguments: dict[str, Any]


# ---------------------------------------------------------------------------
# Level-specific type aliases for documentation clarity
# ---------------------------------------------------------------------------

AgentMiddleware = Middleware[Any]
"""Middleware wrapping one agent.run() call. Context is AgentRunContextProtocol at runtime."""

ChatMiddleware = Middleware[Any]
"""Middleware wrapping each model.generate() call. Context is ChatContextProtocol at runtime."""

FunctionMiddleware = Middleware[Any]
"""Middleware wrapping each tool.execute() call. Context is FunctionContextProtocol at runtime."""


__all__ = [
    "Middleware",
    "AgentMiddleware",
    "ChatMiddleware",
    "FunctionMiddleware",
    "AgentRunContextProtocol",
    "ChatContextProtocol",
    "FunctionContextProtocol",
]

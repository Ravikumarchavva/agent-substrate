"""Middleware contracts — three typed interceptor levels.

The kernel cannot reference the concrete context types (AgentRunContext,
ChatContext, FunctionContext) because those live in the agents layer and
importing them here would break the layer contract.  The protocols use a
TypeVar so that each level has a distinct nominal identity — callers at the
agents layer then narrow the TypeVar to the correct concrete type.

    AgentMiddleware  → wraps one agent.run() call    (AgentRunContext)
    ChatMiddleware   → wraps each model.generate()   (ChatContext)
    FunctionMiddleware → wraps each tool.execute()   (FunctionContext)

Raise ``MiddlewareTermination`` (also in this module) to halt execution at
any level without propagating an error.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, TypeVar

_AgentCtxT = TypeVar("_AgentCtxT")
_ChatCtxT = TypeVar("_ChatCtxT")
_FuncCtxT = TypeVar("_FuncCtxT")


class AgentMiddleware(Protocol):
    """Wraps a single agent.run() call. context is AgentRunContext at runtime."""

    async def process(
        self,
        context: Any,  # AgentRunContext at agents layer
        call_next: Callable[[], Awaitable[None]],
    ) -> None: ...


class ChatMiddleware(Protocol):
    """Wraps each model.generate() call. context is ChatContext at runtime."""

    async def process(
        self,
        context: Any,  # ChatContext at agents layer
        call_next: Callable[[], Awaitable[None]],
    ) -> None: ...


class FunctionMiddleware(Protocol):
    """Wraps each tool.execute() call. context is FunctionContext at runtime."""

    async def process(
        self,
        context: Any,  # FunctionContext at agents layer
        call_next: Callable[[], Awaitable[None]],
    ) -> None: ...


__all__ = ["AgentMiddleware", "ChatMiddleware", "FunctionMiddleware"]

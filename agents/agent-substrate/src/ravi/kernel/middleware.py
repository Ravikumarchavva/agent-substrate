"""Middleware (interceptor) contracts."""

from __future__ import annotations

from typing import Protocol, Callable, Awaitable, Any


from ravi.exceptions import MiddlewareTermination


class AgentMiddleware(Protocol):
    async def process(self, context: Any, call_next: Callable[[], Awaitable[None]]) -> None: ...


class ChatMiddleware(Protocol):
    async def process(self, context: Any, call_next: Callable[[], Awaitable[None]]) -> None: ...


class FunctionMiddleware(Protocol):
    async def process(self, context: Any, call_next: Callable[[], Awaitable[None]]) -> None: ...

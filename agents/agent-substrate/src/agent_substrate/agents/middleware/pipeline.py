"""Generic MiddlewarePipeline."""

from __future__ import annotations

from typing import Generic, TypeVar, Protocol, Callable, Awaitable, Sequence

ContextT = TypeVar("ContextT")
# Middleware only consumes its context (parameter position), so the Protocol's
# type var is contravariant — a Middleware[BaseCtx] is usable as Middleware[SubCtx].
ContextT_contra = TypeVar("ContextT_contra", contravariant=True)


class MiddlewareProtocol(Protocol[ContextT_contra]):
    async def process(
        self, context: ContextT_contra, call_next: Callable[[], Awaitable[None]]
    ) -> None: ...


class MiddlewarePipeline(Generic[ContextT]):
    """Executes a chain of middlewares via call_next() pattern."""

    def __init__(
        self, middlewares: Sequence[MiddlewareProtocol[ContextT]] | None = None
    ) -> None:
        self._middlewares = list(middlewares or [])

    def add(self, middleware: MiddlewareProtocol[ContextT]) -> None:
        self._middlewares.append(middleware)

    async def execute(
        self, context: ContextT, final: Callable[[ContextT], Awaitable[None]]
    ) -> None:
        async def build_chain(idx: int) -> None:
            if idx >= len(self._middlewares):
                await final(context)
                return
            await self._middlewares[idx].process(context, lambda: build_chain(idx + 1))

        await build_chain(0)


__all__ = ["MiddlewarePipeline"]

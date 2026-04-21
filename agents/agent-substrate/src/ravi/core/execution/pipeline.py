"""Shared middleware execution engine.

Both agent middleware and workflow middleware use the same ordering and
error semantics through this internal generic runner.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Generic, List, Optional, Protocol, TypeVar

logger = logging.getLogger(__name__)

ContextT = TypeVar("ContextT")


class _ExecutionMiddleware(Protocol[ContextT]):
    name: str

    async def before(self, ctx: ContextT) -> ContextT: ...

    async def after(self, ctx: ContextT, result: Any) -> Any: ...

    async def on_error(self, ctx: ContextT, error: Exception) -> Optional[Any]: ...


MiddlewareT = TypeVar("MiddlewareT", bound=_ExecutionMiddleware[Any])


class ExecutionMiddlewarePipeline(Generic[ContextT, MiddlewareT]):
    """Sequential middleware runner with consistent semantics."""

    def __init__(self, middleware: Optional[List[MiddlewareT]] = None) -> None:
        self._middleware: List[MiddlewareT] = list(middleware or [])

    @property
    def middleware(self) -> List[MiddlewareT]:
        return self._middleware

    def add(self, middleware: MiddlewareT) -> None:
        self._middleware.append(middleware)

    async def run(
        self,
        ctx: ContextT,
        execute_fn: Callable[[ContextT], Awaitable[Any]],
    ) -> Any:
        if not self._middleware:
            return await execute_fn(ctx)

        for middleware in self._middleware:
            try:
                ctx = await middleware.before(ctx)
            except Exception:
                logger.exception(
                    "Execution middleware %r before() failed",
                    getattr(middleware, "name", type(middleware).__name__),
                )
                raise

        try:
            result = await execute_fn(ctx)
        except Exception as exc:
            fallback: Any = None
            for middleware in reversed(self._middleware):
                try:
                    maybe_result = await middleware.on_error(ctx, exc)
                    if maybe_result is not None and fallback is None:
                        fallback = maybe_result
                except Exception:
                    logger.exception(
                        "Execution middleware %r on_error() failed",
                        getattr(middleware, "name", type(middleware).__name__),
                    )
            if fallback is None:
                raise
            result = fallback

        for middleware in reversed(self._middleware):
            try:
                result = await middleware.after(ctx, result)
            except Exception:
                logger.exception(
                    "Execution middleware %r after() failed",
                    getattr(middleware, "name", type(middleware).__name__),
                )
                raise

        return result

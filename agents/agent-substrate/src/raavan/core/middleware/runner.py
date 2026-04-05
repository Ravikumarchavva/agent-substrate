"""Middleware pipeline runner.

Executes an ordered list of ``BaseMiddleware`` instances around a
callable (the LLM call or tool execution).

Execution order:
  1. ``before()`` — called in **forward order** (first middleware first).
  2. ``execute_fn()`` — the actual work (LLM call, tool run).
  3. ``after()``  — called in **reverse order** (last middleware first).

On error:
  - ``on_error()`` is called on every middleware in **reverse order**.
  - If any ``on_error()`` returns a non-``None`` value, that value
    replaces the result and the exception is suppressed.
  - Otherwise the original exception is re-raised.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, List, Optional

from raavan.core.execution.pipeline import ExecutionMiddlewarePipeline
from raavan.core.middleware.base import BaseMiddleware, MiddlewareContext

logger = logging.getLogger(__name__)


class MiddlewarePipeline:
    """Sequential middleware execution engine."""

    def __init__(self, middleware: Optional[List[BaseMiddleware]] = None) -> None:
        self._pipeline = ExecutionMiddlewarePipeline[MiddlewareContext, BaseMiddleware](
            middleware
        )

    @property
    def middleware(self) -> List[BaseMiddleware]:
        return self._pipeline.middleware

    def add(self, mw: BaseMiddleware) -> None:
        """Append a middleware to the end of the pipeline."""
        self._pipeline.add(mw)

    async def run(
        self,
        ctx: MiddlewareContext,
        execute_fn: Callable[[MiddlewareContext], Awaitable[Any]],
    ) -> Any:
        """Run the full before → execute → after chain.

        Args:
            ctx: The mutable middleware context.
            execute_fn: The actual work to run (e.g. LLM generate call).

        Returns:
            The (possibly transformed) result.

        Raises:
            Exception: Re-raises if no ``on_error`` handler suppresses it.
        """
        return await self._pipeline.run(ctx, execute_fn)

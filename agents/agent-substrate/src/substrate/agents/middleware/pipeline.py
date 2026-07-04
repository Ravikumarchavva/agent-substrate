"""MiddlewarePipeline — dispatches the one Middleware shape, stage-filtered."""

from __future__ import annotations

from typing import Awaitable, Callable, Sequence

from substrate.agents.middleware._contracts import MiddlewareContext
from substrate.kernel.agent.middleware import Middleware, MiddlewareStage

_ALL_STAGES = frozenset(MiddlewareStage)


class MiddlewarePipeline:
    """Executes a chain of middlewares via the call_next() pattern.

    Every middleware in the pipeline implements the identical
    ``Middleware.process(context, call_next)`` shape — there is no
    per-stage pipeline variant. A middleware that only cares about one
    stage (or a few) declares that via a class-level ``stages`` attribute
    (a ``frozenset[MiddlewareStage]``); this pipeline skips calling
    ``process()`` for any stage a middleware didn't declare, so a
    TOOL-only middleware never runs — not even a no-op pass-through —
    during a TURN or CHAT dispatch. A middleware that omits ``stages``
    entirely runs at every stage.
    """

    def __init__(self, middlewares: Sequence[Middleware] | None = None) -> None:
        self._middlewares = list(middlewares or [])

    def add(self, middleware: Middleware) -> None:
        self._middlewares.append(middleware)

    async def execute(
        self,
        context: MiddlewareContext,
        final: Callable[[MiddlewareContext], Awaitable[None]],
    ) -> None:
        active = [
            mw
            for mw in self._middlewares
            if context.stage in getattr(mw, "stages", _ALL_STAGES)
        ]

        async def build_chain(idx: int) -> None:
            if idx >= len(active):
                await final(context)
                return
            await active[idx].process(context, lambda: build_chain(idx + 1))

        await build_chain(0)


__all__ = ["MiddlewarePipeline"]

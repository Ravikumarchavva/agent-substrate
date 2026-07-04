"""Middleware contract — one interceptor shape, one context shape.

A middleware wraps a moment in an agent's execution: one inbox turn, one LLM
call, or one tool call. All three moments share the exact same interceptor
shape and the exact same context shape — ``MiddlewareStage`` says *which*
moment a given context instance represents; it is not a different kind of
middleware, just a value a middleware can read.

Raise ``MiddlewareTermination`` (see ``substrate.kernel.core.errors``) from
any ``process`` implementation to halt execution cleanly at that point.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Awaitable, Callable, Protocol


class MiddlewareStage(str, Enum):
    """Which moment of an agent's execution a ``MiddlewareContext`` represents.

    TURN — one inbox message (one call to the agent's message handler).
    CHAT — one LLM generation call.
    TOOL — one tool invocation.
    """

    TURN = "turn"
    CHAT = "chat"
    TOOL = "tool"


class MiddlewareContextProtocol(Protocol):
    """Minimal shape the kernel can type-check without importing the
    concrete ``MiddlewareContext`` dataclass (defined at the agents layer,
    which carries the full set of stage-specific fields)."""

    stage: MiddlewareStage
    agent_name: str
    run_id: str
    metadata: dict[str, Any]


class Middleware(Protocol):
    """The one interceptor shape — call_next() continues the chain.

    Every middleware in the framework implements exactly this: no
    per-stage variants. A middleware that only cares about one stage
    declares that via a ``stages`` class attribute (see
    ``agents/middleware/pipeline.py``); this Protocol itself makes no
    distinction between stages.
    """

    async def process(
        self,
        context: MiddlewareContextProtocol,
        call_next: Callable[[], Awaitable[None]],
    ) -> None: ...


__all__ = [
    "MiddlewareStage",
    "MiddlewareContextProtocol",
    "Middleware",
]

"""Middleware stage marker.

A middleware wraps a moment in an agent's execution: one inbox turn, one LLM
call, or one tool call. All three moments share the exact same interceptor
shape and the exact same context shape — ``MiddlewareStage`` says *which*
moment a given context instance represents; it is not a different kind of
middleware, just a value a middleware can read.

The interceptor shape itself (``Middleware`` Protocol) and the context shape
(``MiddlewareContext`` dataclass) live in ``agents/middleware/_contracts.py``,
not here — every real middleware implementation needs the concrete context's
stage-specific fields (``turn_result``/``chat_result``/``tool_result``, …),
so a kernel-minimal duplicate of the Protocol would have zero real
consumers (a prior version of this module had one; nothing outside kernel's
own re-export ever imported it). ``MiddlewareStage`` itself is a plain,
dependency-free enum that every middleware implementation across the agents
layer needs, which is exactly the kind of small shared value kernel exists
to hold — so it stays here on its own.

Raise ``MiddlewareTermination`` (see ``substrate.kernel.core.errors``) from
any ``process`` implementation to halt execution cleanly at that point.
"""

from __future__ import annotations

from enum import Enum


class MiddlewareStage(str, Enum):
    """Which moment of an agent's execution a ``MiddlewareContext`` represents.

    TURN — one inbox message (one call to the agent's message handler).
    CHAT — one LLM generation call.
    TOOL — one tool invocation.
    """

    TURN = "turn"
    CHAT = "chat"
    TOOL = "tool"


__all__ = ["MiddlewareStage"]

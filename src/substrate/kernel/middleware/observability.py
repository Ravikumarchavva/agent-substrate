"""Tracing spans for each of the three middleware stages.

Wraps agent turns, LLM calls, and tool calls in lightweight OpenTelemetry
spans (or DEBUG-level log statements when OTel is not configured). Each
class is an ordinary middleware — the only thing distinguishing them is the
``stages`` they declare.

Usage::

    from substrate.agents.middleware.observability import (
        AgentTracingMiddleware,
        ChatTracingMiddleware,
        FunctionTracingMiddleware,
    )
    from substrate.agents.middleware.pipeline import MiddlewarePipeline

    agent = ReActAgent(
        "bot", model=client,
        middleware=MiddlewarePipeline([
            AgentTracingMiddleware(), ChatTracingMiddleware(), FunctionTracingMiddleware(),
        ]),
    )
"""

from __future__ import annotations

import time
from typing import Callable, Awaitable, ClassVar

from substrate.agents.middleware._contracts import MiddlewareContext
from substrate.kernel.agent.middleware import MiddlewareStage
from substrate.logger import setup_logging

logger = setup_logging()


def _otel_span(name: str) -> object | None:
    """Return an OTel span if opentelemetry is installed, else None."""
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]

        tracer = trace.get_tracer("substrate.agents")
        return tracer.start_span(name)
    except Exception:
        return None


class AgentTracingMiddleware:
    """Wraps each agent turn (one inbox message) in a tracing span.

    Records: agent_name, run_id, session_id, final status, duration.
    """

    stages: ClassVar[frozenset[MiddlewareStage]] = frozenset({MiddlewareStage.TURN})

    async def process(
        self,
        context: MiddlewareContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        span = _otel_span(f"agent.run/{context.agent_name}")
        if span is not None:
            span.set_attribute("agent.name", context.agent_name)  # type: ignore[attr-defined]
            span.set_attribute("run.id", context.run_id)  # type: ignore[attr-defined]
            span.set_attribute("session.id", context.session_id)  # type: ignore[attr-defined]
        t0 = time.monotonic()
        logger.debug(
            "[trace] agent.run START agent=%r run_id=%s session=%s",
            context.agent_name,
            context.run_id,
            context.session_id,
        )
        try:
            if span:
                with span:  # type: ignore[attr-defined]
                    await call_next()
            else:
                await call_next()
            elapsed_ms = (time.monotonic() - t0) * 1000
            status = context.turn_result.status if context.turn_result else "unknown"
            if span is not None:
                span.set_attribute("status", status)  # type: ignore[attr-defined]
            logger.debug(
                "[trace] agent.run END agent=%r run_id=%s status=%s elapsed=%.1fms",
                context.agent_name,
                context.run_id,
                status,
                elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if span is not None:
                span.set_attribute("error", True)  # type: ignore[attr-defined]
                span.set_attribute("error.type", type(exc).__name__)  # type: ignore[attr-defined]
            logger.debug(
                "[trace] agent.run ERROR agent=%r run_id=%s error=%s elapsed=%.1fms",
                context.agent_name,
                context.run_id,
                type(exc).__name__,
                elapsed_ms,
            )
            raise


class ChatTracingMiddleware:
    """Wraps each LLM call (``ctx.llm()``) in a tracing span.

    Records: agent_name, run_id, message count, token usage, duration.
    """

    stages: ClassVar[frozenset[MiddlewareStage]] = frozenset({MiddlewareStage.CHAT})

    async def process(
        self,
        context: MiddlewareContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        span = _otel_span(f"agent.llm_call/{context.agent_name}")
        if span is not None:
            span.set_attribute("agent.name", context.agent_name)  # type: ignore[attr-defined]
            span.set_attribute("run.id", context.run_id)  # type: ignore[attr-defined]
        t0 = time.monotonic()
        logger.debug(
            "[trace] llm_call START agent=%r run_id=%s msgs=%d",
            context.agent_name,
            context.run_id,
            len(context.messages or []),
        )
        try:
            if span:
                with span:  # type: ignore[attr-defined]
                    await call_next()
            else:
                await call_next()
            elapsed_ms = (time.monotonic() - t0) * 1000
            tokens = (
                context.chat_result.usage.total_tokens if context.chat_result else 0
            )
            if span is not None:
                span.set_attribute("llm.tokens", tokens)  # type: ignore[attr-defined]
            logger.debug(
                "[trace] llm_call END agent=%r run_id=%s tokens=%d elapsed=%.1fms",
                context.agent_name,
                context.run_id,
                tokens,
                elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if span is not None:
                span.set_attribute("error", True)  # type: ignore[attr-defined]
                span.set_attribute("error.type", type(exc).__name__)  # type: ignore[attr-defined]
            logger.debug(
                "[trace] llm_call ERROR agent=%r run_id=%s error=%s elapsed=%.1fms",
                context.agent_name,
                context.run_id,
                type(exc).__name__,
                elapsed_ms,
            )
            raise


class FunctionTracingMiddleware:
    """Wraps each tool call (``ctx.tool()``) in a tracing span.

    Records: agent_name, run_id, tool name, ok/error, duration.
    """

    stages: ClassVar[frozenset[MiddlewareStage]] = frozenset({MiddlewareStage.TOOL})

    async def process(
        self,
        context: MiddlewareContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        span = _otel_span(f"agent.tool_call/{context.function_name}")
        if span is not None:
            span.set_attribute("agent.name", context.agent_name)  # type: ignore[attr-defined]
            span.set_attribute("run.id", context.run_id)  # type: ignore[attr-defined]
            span.set_attribute("tool.name", context.function_name)  # type: ignore[attr-defined]
        t0 = time.monotonic()
        logger.debug(
            "[trace] tool_call START agent=%r run_id=%s tool=%r",
            context.agent_name,
            context.run_id,
            context.function_name,
        )
        try:
            if span:
                with span:  # type: ignore[attr-defined]
                    await call_next()
            else:
                await call_next()
            elapsed_ms = (time.monotonic() - t0) * 1000
            ok = context.tool_result.status == "ok" if context.tool_result else False
            if span is not None:
                span.set_attribute("tool.ok", ok)  # type: ignore[attr-defined]
            logger.debug(
                "[trace] tool_call END agent=%r run_id=%s tool=%r ok=%s elapsed=%.1fms",
                context.agent_name,
                context.run_id,
                context.function_name,
                ok,
                elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if span is not None:
                span.set_attribute("error", True)  # type: ignore[attr-defined]
                span.set_attribute("error.type", type(exc).__name__)  # type: ignore[attr-defined]
            logger.debug(
                "[trace] tool_call ERROR agent=%r run_id=%s tool=%r error=%s elapsed=%.1fms",
                context.agent_name,
                context.run_id,
                context.function_name,
                type(exc).__name__,
                elapsed_ms,
            )
            raise


__all__ = [
    "AgentTracingMiddleware",
    "ChatTracingMiddleware",
    "FunctionTracingMiddleware",
]

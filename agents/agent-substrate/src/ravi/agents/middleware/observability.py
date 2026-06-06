"""ObservabilityMiddleware — tracing spans as AgentMiddleware and ChatMiddleware.

Wraps agent runs and LLM calls with lightweight OpenTelemetry spans (or
DEBUG-level log statements when OTel is not configured).

Usage::

    from ravi.agents.middleware.observability import (
        AgentTracingMiddleware,
        ChatTracingMiddleware,
    )

    agent = ReActAgent(
        "bot", runtime, model=client,
        agent_middleware=[AgentTracingMiddleware()],
        chat_middleware=[ChatTracingMiddleware()],
    )
"""

from __future__ import annotations

import time
from typing import Callable, Awaitable

from ravi.agents.middleware._contracts import AgentRunContext, ChatContext
from ravi.logger import setup_logging

logger = setup_logging()


def _otel_span(name: str) -> object | None:
    """Return an OTel span if opentelemetry is installed, else None."""
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]

        tracer = trace.get_tracer("ravi.agents")
        return tracer.start_span(name)
    except Exception:
        return None


class AgentTracingMiddleware:
    """Wraps each agent.run() in a tracing span.

    Records: agent_name, run_id, session_id, final status, duration.
    """

    async def process(
        self,
        context: AgentRunContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        span = _otel_span(f"agent.run/{context.agent_name}")
        t0 = time.monotonic()
        logger.debug(
            "[trace] agent.run START agent=%r run_id=%s session=%s",
            context.agent_name, context.run_id, context.session_id,
        )
        try:
            if span:
                with span:  # type: ignore[attr-defined]
                    await call_next()
            else:
                await call_next()
            elapsed_ms = (time.monotonic() - t0) * 1000
            status = context.result.status if context.result else "unknown"
            logger.debug(
                "[trace] agent.run END agent=%r run_id=%s status=%s elapsed=%.1fms",
                context.agent_name, context.run_id, status, elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.debug(
                "[trace] agent.run ERROR agent=%r run_id=%s error=%s elapsed=%.1fms",
                context.agent_name, context.run_id, type(exc).__name__, elapsed_ms,
            )
            raise


class ChatTracingMiddleware:
    """Wraps each model.generate() call in a tracing span.

    Records: agent_name, run_id, message count, token usage, duration.
    """

    async def process(
        self,
        context: ChatContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        span = _otel_span(f"agent.llm_call/{context.agent_name}")
        t0 = time.monotonic()
        logger.debug(
            "[trace] llm_call START agent=%r run_id=%s msgs=%d",
            context.agent_name, context.run_id, len(context.messages),
        )
        try:
            if span:
                with span:  # type: ignore[attr-defined]
                    await call_next()
            else:
                await call_next()
            elapsed_ms = (time.monotonic() - t0) * 1000
            tokens = context.result.usage.total_tokens if context.result else 0
            logger.debug(
                "[trace] llm_call END agent=%r run_id=%s tokens=%d elapsed=%.1fms",
                context.agent_name, context.run_id, tokens, elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.debug(
                "[trace] llm_call ERROR agent=%r run_id=%s error=%s elapsed=%.1fms",
                context.agent_name, context.run_id, type(exc).__name__, elapsed_ms,
            )
            raise


__all__ = ["AgentTracingMiddleware", "ChatTracingMiddleware"]

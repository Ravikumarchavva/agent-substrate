"""Tests for the middleware pipeline and concrete middleware implementations."""

from __future__ import annotations

import logging
import pytest
from ravi.kernel.core.content import ChatMessage, TextBlock
from ravi.agents.middleware import (
    MiddlewarePipeline,
    AuditLoggerMiddleware,
    AgentCallContext,
    AgentRunResult,
)
from ravi.exceptions import MiddlewareTermination


def _ctx(text: str = "hello") -> AgentCallContext:
    msg = ChatMessage(role="user", content=[TextBlock(text=text)])
    return AgentCallContext(
        agent_name="TestAgent", run_id="r1", session_id="s1", messages=[msg]
    )


# ---------------------------------------------------------------------------
# MiddlewarePipeline — call_next chaining
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_calls_final():
    """Empty pipeline calls final directly."""
    pipeline = MiddlewarePipeline([])
    called = []

    async def final(ctx: AgentCallContext) -> None:
        called.append(ctx.agent_name)

    await pipeline.execute(_ctx(), final)
    assert called == ["TestAgent"]


@pytest.mark.asyncio
async def test_pipeline_chains_middlewares_in_order():
    """Middlewares execute pre-call_next in registration order, post in reverse."""
    order: list[str] = []

    class RecordMiddleware:
        def __init__(self, name: str) -> None:
            self._name = name

        async def process(self, context: AgentCallContext, call_next) -> None:
            order.append(f"{self._name}:before")
            await call_next()
            order.append(f"{self._name}:after")

    pipeline = MiddlewarePipeline([RecordMiddleware("A"), RecordMiddleware("B")])

    async def final(ctx: AgentCallContext) -> None:
        order.append("final")

    await pipeline.execute(_ctx(), final)
    assert order == ["A:before", "B:before", "final", "B:after", "A:after"]


@pytest.mark.asyncio
async def test_pipeline_halts_on_middleware_termination():
    """Raising MiddlewareTermination stops the chain — final and later middlewares skip."""
    reached_final = []
    reached_b = []

    class BlockingMiddleware:
        async def process(self, context: AgentCallContext, call_next) -> None:
            raise MiddlewareTermination("blocked")

    class TrailingMiddleware:
        async def process(self, context: AgentCallContext, call_next) -> None:
            reached_b.append(True)
            await call_next()

    pipeline = MiddlewarePipeline([BlockingMiddleware(), TrailingMiddleware()])

    with pytest.raises(MiddlewareTermination):
        await pipeline.execute(_ctx(), lambda c: reached_final.append(True))

    assert not reached_final
    assert not reached_b


@pytest.mark.asyncio
async def test_pipeline_middleware_can_mutate_context():
    """Middleware can mutate context before calling next."""

    class AddMessageMiddleware:
        async def process(self, context: AgentCallContext, call_next) -> None:
            context.metadata["injected"] = True
            await call_next()

    async def noop(c: AgentCallContext) -> None:
        pass

    pipeline = MiddlewarePipeline([AddMessageMiddleware()])
    ctx = _ctx()
    await pipeline.execute(ctx, noop)
    assert ctx.metadata.get("injected") is True


# ---------------------------------------------------------------------------
# AuditLoggerMiddleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_logger_logs_run(caplog):
    ravi_logger = logging.getLogger("ravi")
    ravi_logger.addHandler(caplog.handler)

    mw = AuditLoggerMiddleware(log_level=logging.INFO)
    ctx = _ctx("audit test")

    result_holder: list[AgentRunResult] = []

    async def final(c: AgentCallContext) -> None:
        c.result = AgentRunResult(output="done", status="success", run_id="r1")
        result_holder.append(c.result)

    with caplog.at_level(logging.INFO, logger="ravi"):
        await MiddlewarePipeline([mw]).execute(ctx, final)

    assert "RUN START" in caplog.text
    assert "RUN END" in caplog.text
    assert result_holder

    ravi_logger.removeHandler(caplog.handler)

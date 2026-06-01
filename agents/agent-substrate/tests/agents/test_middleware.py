from __future__ import annotations

import logging
import pytest
from ravi.kernel.content import ChatMessage, TextBlock
from ravi.agents.middleware import MiddlewarePipeline, AuditLoggerMiddleware
from ravi.agents.middleware._contracts import MiddlewareContext, MiddlewareStage


class DummyInterceptor:
    async def pre_process(self, message: ChatMessage) -> ChatMessage:
        content = list(message.content)
        content.append(TextBlock(text="pre"))
        return ChatMessage(role=message.role, content=content)

    async def post_process(self, message: ChatMessage) -> ChatMessage:
        content = list(message.content)
        content.append(TextBlock(text="post"))
        return ChatMessage(role=message.role, content=content)


@pytest.mark.asyncio
async def test_middleware_pipeline():
    pipeline = MiddlewarePipeline()
    interceptor = DummyInterceptor()
    pipeline.add(interceptor)

    msg = ChatMessage(role="user", content=[TextBlock(text="original")])
    
    res_pre = await pipeline.execute_pre(msg)
    assert len(res_pre.content) == 2
    assert res_pre.content[0].text == "original"
    assert res_pre.content[1].text == "pre"

    res_post = await pipeline.execute_post(msg)
    assert len(res_post.content) == 2
    assert res_post.content[0].text == "original"
    assert res_post.content[1].text == "post"


@pytest.mark.asyncio
async def test_audit_logger_middleware(caplog):
    # Ensure logs from 'ravi' namespace propagate or are captured by pytest
    ravi_logger = logging.getLogger("ravi")
    ravi_logger.addHandler(caplog.handler)

    middleware = AuditLoggerMiddleware(log_level=logging.INFO)
    ctx = MiddlewareContext(
        agent_name="TestAgent",
        stage=MiddlewareStage.LLM_CALL,
        input_text="hello audit",
    )

    with caplog.at_level(logging.INFO, logger="ravi"):
        # Test before
        ctx_out = await middleware.before(ctx)
        assert "_audit_t0" in ctx_out.metadata
        assert "[audit] llm_call START" in caplog.text

        # Test after
        caplog.clear()
        res = await middleware.after(ctx_out, "some_result")
        assert res == "some_result"
        assert "[audit] llm_call END" in caplog.text

        # Test error
        caplog.clear()
        await middleware.on_error(ctx_out, ValueError("some error"))
        assert "[audit] llm_call ERROR" in caplog.text
        assert "ValueError" in caplog.text

    # Cleanup handler
    ravi_logger.removeHandler(caplog.handler)

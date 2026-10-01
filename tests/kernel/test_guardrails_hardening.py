"""Guardrails must look at everything that matters, and retries must not
re-run what can never succeed."""

from __future__ import annotations

import pytest

from substrate.agents.middleware._contracts import MiddlewareContext
from substrate.agents.middleware.guardrails.max_token import MaxTokenMiddleware
from substrate.agents.middleware.guardrails.pii import PIIDetectionMiddleware
from substrate.agents.middleware.retry import RetryMiddleware
from substrate.exceptions import MiddlewareTermination
from substrate.kernel.agent.middleware import MiddlewareStage
from substrate.kernel.core.content import (
    ChatMessage,
    MediaBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from substrate.kernel.exceptions import BudgetExhaustedError, PermanentError, TransientError


async def _pass() -> None:
    return None


# ── MaxToken ─────────────────────────────────────────────────────────────────


def _chat(messages: list[ChatMessage], system: str = "") -> MiddlewareContext:
    return MiddlewareContext(
        stage=MiddlewareStage.CHAT,
        agent_name="a",
        run_id="r",
        messages=messages,
        system_instructions=system,
    )


async def test_max_token_counts_tool_results_not_just_top_level_text():
    """Regression: only top-level TextBlocks were counted, so a huge tool
    result (most of an agent's context) sailed under the limit."""
    huge = ChatMessage(
        role=Role.TOOL,
        content=[ToolResultBlock(call_id="c", content=[TextBlock(text="x" * 40_000)])],
    )
    with pytest.raises(MiddlewareTermination):
        await MaxTokenMiddleware(max_tokens=1_000).process(_chat([huge]), _pass)


async def test_max_token_counts_images_tool_arguments_and_the_system_prompt():
    image = ChatMessage(role=Role.USER, content=[MediaBlock.image(data=b"x", media_type="image/png")])
    call = ChatMessage(
        role=Role.ASSISTANT,
        content=[ToolUseBlock(call_id="c", tool_name="run", arguments={"code": "y" * 20_000})],
    )
    mw = MaxTokenMiddleware(max_tokens=500)

    with pytest.raises(MiddlewareTermination):
        await mw.process(_chat([image]), _pass)  # ~1000 tokens of image
    with pytest.raises(MiddlewareTermination):
        await mw.process(_chat([call]), _pass)  # ~5000 tokens of code
    with pytest.raises(MiddlewareTermination):
        await mw.process(_chat([], system="s" * 4_000), _pass)


async def test_max_token_lets_a_small_context_through():
    small = ChatMessage(role=Role.USER, content=[TextBlock(text="hello")])
    await MaxTokenMiddleware(max_tokens=100).process(_chat([small]), _pass)


# ── PII ──────────────────────────────────────────────────────────────────────


def _tool_ctx(arguments: dict) -> MiddlewareContext:
    return MiddlewareContext(
        stage=MiddlewareStage.TOOL, agent_name="a", run_id="r", function_name="send", arguments=arguments
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"to": "ana@example.com"},
        {"body": {"contact": {"email": "ana@example.com"}}},
        {"recipients": ["bob@example.com"]},
        {"rows": [{"notes": ["call 555-123-4567"]}]},
    ],
)
async def test_pii_is_found_however_deeply_it_is_nested(arguments: dict):
    with pytest.raises(MiddlewareTermination):
        await PIIDetectionMiddleware().process(_tool_ctx(arguments), _pass)


async def test_pii_ignores_clean_nested_arguments():
    await PIIDetectionMiddleware().process(_tool_ctx({"body": {"n": 3, "tags": ["a", "b"]}}), _pass)


# ── Retry ────────────────────────────────────────────────────────────────────


class _Flaky:
    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)


class _Status(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _retry() -> RetryMiddleware:
    return RetryMiddleware(max_retries=3, base_delay=0.0, jitter=0.0)


async def test_retry_recovers_from_a_transient_error():
    flaky = _Flaky(_Status(503), TransientError("busy"))
    await _retry().process(_chat([]), flaky)
    assert flaky.calls == 3


@pytest.mark.parametrize(
    "error",
    [_Status(401), _Status(400), PermanentError("nope"), BudgetExhaustedError("spent"), MiddlewareTermination("blocked")],
)
async def test_retry_does_not_repeat_what_can_never_succeed(error: Exception):
    flaky = _Flaky(error)
    with pytest.raises(type(error)):
        await _retry().process(_chat([]), flaky)
    assert flaky.calls == 1

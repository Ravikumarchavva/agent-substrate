"""Invariant register — every provider outcome is typed (row I21).

The audit found ``finish_reason``, ``retry_after`` and refusals read nowhere, so a reply cut off
at ``max_tokens`` looked exactly like a finished one, a rate limit retried on the wrong clock, and
a context overflow was an undifferentiated 400. Each outcome now has a type the engine acts on:

* every client reports why the model stopped, and the engine marks a truncated reply truncated;
* a rate limit carries the provider's ``Retry-After``;
* a context overflow is its own error, and the agent shrinks the prompt and tries once more;
* a content-filter stop is a typed failure, not an empty answer.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from substrate.types import ChatMessage, ContentBlock, Role, TextBlock
from substrate.types.finish_reason import FinishReason
from substrate.types import Actor
from substrate.types import Usage
from substrate.types import (
    AuthError,
    ContentFilterError,
    ContextLengthError,
    PermanentError,
    RateLimitedError,
)
from substrate.models import GenerationOptions, ModelCapabilities
from substrate.runtime import ChatPayload, Message
from substrate.types import CompletionEvent
from substrate.types import RunLogKind
from substrate.runtime import RunRetryPolicy
from substrate.agents import ReActAgent
from substrate.models.errors import classify_llm_error
from substrate.testing.runtime import ephemeral_runtime

# ---------------------------------------------------------------------------- classification


class _SdkError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(headers=headers or {})


def test_a_rate_limit_carries_the_providers_retry_after() -> None:
    err = classify_llm_error(_SdkError("slow down", 429, {"retry-after": "7"}))
    assert isinstance(err, RateLimitedError) and err.retry_after == 7.0


def test_retry_after_in_milliseconds_is_understood() -> None:
    err = classify_llm_error(_SdkError("slow down", 429, {"retry-after-ms": "1500"}))
    assert isinstance(err, RateLimitedError) and err.retry_after == pytest.approx(1.5)


def test_a_rate_limit_without_a_hint_still_types_as_rate_limited() -> None:
    err = classify_llm_error(_SdkError("slow down", 429))
    assert (
        isinstance(err, RateLimitedError) and err.retry_after is None and err.retryable
    )


@pytest.mark.parametrize(
    "message",
    [
        "This model's maximum context length is 8192 tokens",
        "prompt is too long: 250000 tokens > 200000 maximum",
        "context_length_exceeded",
        "The input token count (1200000) exceeds the maximum number of tokens allowed",
    ],
)
def test_a_context_overflow_is_distinguishable_from_any_other_bad_request(
    message: str,
) -> None:
    assert isinstance(classify_llm_error(_SdkError(message, 400)), ContextLengthError)


def test_a_wrapped_sdk_error_is_still_classified() -> None:
    """Clients wrap the SDK's exception in their own; the status and headers live on the cause."""
    try:
        try:
            raise _SdkError("slow down", 429, {"retry-after": "3"})
        except _SdkError as cause:
            raise RuntimeError("HTTP 429") from cause
    except RuntimeError as wrapped:
        err = classify_llm_error(wrapped)
    assert isinstance(err, RateLimitedError) and err.retry_after == 3.0


def test_credentials_content_filter_and_other_client_errors_are_typed() -> None:
    assert isinstance(classify_llm_error(_SdkError("bad key", 401)), AuthError)
    assert isinstance(
        classify_llm_error(_SdkError("blocked by content management policy", 400)),
        ContentFilterError,
    )
    assert isinstance(
        classify_llm_error(_SdkError("unknown model", 404)), PermanentError
    )


def test_a_server_error_stays_transient() -> None:
    err = _SdkError("upstream down", 503)
    assert classify_llm_error(err) is err


# ---------------------------------------------------------------------------- vendor mapping


def test_every_vendor_reports_why_the_model_stopped() -> None:
    from substrate.integrations.llm.anthropic.anthropic_client import (
        anthropic_finish_reason,
    )
    from substrate.integrations.llm.gemini.gemini_client import gemini_finish_reason
    from substrate.integrations.llm.openai.openai_client import responses_finish_reason
    from substrate.integrations.llm.openai_compatible import chat_finish_reason
    from substrate.types import ToolUseBlock

    assert chat_finish_reason("stop", has_tool_calls=False) is FinishReason.STOP
    assert chat_finish_reason("length", has_tool_calls=False) is FinishReason.LENGTH
    assert (
        chat_finish_reason("tool_calls", has_tool_calls=True) is FinishReason.TOOL_CALLS
    )
    assert chat_finish_reason("stop", has_tool_calls=True) is FinishReason.TOOL_CALLS
    assert (
        chat_finish_reason("content_filter", has_tool_calls=False)
        is FinishReason.CONTENT_FILTER
    )
    assert chat_finish_reason(None, has_tool_calls=False) is FinishReason.UNSPECIFIED, (
        "silence is a client bug, not a stop"
    )

    assert anthropic_finish_reason("end_turn") is FinishReason.STOP
    assert anthropic_finish_reason("max_tokens") is FinishReason.LENGTH
    assert anthropic_finish_reason("tool_use") is FinishReason.TOOL_CALLS
    assert anthropic_finish_reason("refusal") is FinishReason.REFUSAL
    assert anthropic_finish_reason(None) is FinishReason.UNSPECIFIED

    assert gemini_finish_reason("STOP", has_tool_calls=False) is FinishReason.STOP
    assert gemini_finish_reason("STOP", has_tool_calls=True) is FinishReason.TOOL_CALLS
    assert (
        gemini_finish_reason(SimpleNamespace(name="MAX_TOKENS"), has_tool_calls=False)
        is FinishReason.LENGTH
    )
    assert (
        gemini_finish_reason("SAFETY", has_tool_calls=False)
        is FinishReason.CONTENT_FILTER
    )
    assert gemini_finish_reason(None, has_tool_calls=False) is FinishReason.UNSPECIFIED

    done = SimpleNamespace(status="completed", incomplete_details=None, output=[])
    cut = SimpleNamespace(
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        output=[],
    )
    filtered = SimpleNamespace(
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="content_filter"),
        output=[],
    )
    assert responses_finish_reason(done, [TextBlock(text="x")]) is FinishReason.STOP
    assert (
        responses_finish_reason(
            done, [ToolUseBlock(call_id="1", tool_name="t", arguments={})]
        )
        is FinishReason.TOOL_CALLS
    )
    assert responses_finish_reason(cut, []) is FinishReason.LENGTH
    assert responses_finish_reason(filtered, []) is FinishReason.CONTENT_FILTER


# ---------------------------------------------------------------------------- engine behaviour


class _LLM:
    """Plays back turns: an exception to raise, or ``(blocks, finish_reason)`` to answer with."""

    def __init__(self, turns: list[Any]) -> None:
        self.model = "scripted"
        self.capabilities = ModelCapabilities(model_id="scripted")
        self._turns = list(turns)
        self.seen: list[int] = []

    async def generate(
        self, messages: Any, *, options: Any = None, ctx: Any = None
    ) -> Any:
        raise NotImplementedError

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
        ctx: Any = None,
    ) -> AsyncIterator[CompletionEvent]:  # noqa: B008
        return self._stream(messages)

    async def _stream(
        self, messages: list[ChatMessage]
    ) -> AsyncIterator[CompletionEvent]:
        self.seen.append(len(messages))
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        blocks, reason = turn
        yield CompletionEvent(
            content=blocks,
            usage=Usage(input_tokens=5, output_tokens=5),
            finish_reason=reason,
        )

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return 0


async def _run(llm: _LLM, *, retries: int = 0) -> tuple[str, dict[str, Any], list[str]]:
    return await _run_with(
        ReActAgent("bot", model=llm, max_iterations=3), retries=retries
    )


async def _run_with(
    agent: ReActAgent, *, retries: int = 0
) -> tuple[str, dict[str, Any], list[str]]:
    async with ephemeral_runtime() as rt:
        await rt.register(agent)
        msg = Message(
            target=agent.id,
            sender=Actor.system("t"),
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text="hi")])
            ),
        )
        run_id = await rt.submit(
            agent.id,
            msg,
            retry_policy=RunRetryPolicy(max_retries=retries, backoff_s=0.0),
        )

        async def watch() -> tuple[str, dict[str, Any]]:
            async for entry in rt.tail(run_id):
                if entry.kind in (RunLogKind.RUN_COMPLETED, RunLogKind.RUN_FAILED):
                    return str(entry.kind), dict(entry.payload or {})
            raise AssertionError("no terminal entry")

        kind, payload = await asyncio.wait_for(watch(), 10)
        return kind, payload, [str(e.kind) for e in await rt.read(run_id)]


def _text(
    text: str, reason: FinishReason = FinishReason.STOP
) -> tuple[list[ContentBlock], FinishReason]:
    return [TextBlock(text=text)], reason


async def test_i21_a_reply_cut_off_at_the_token_limit_is_marked_truncated() -> None:
    kind, _, kinds = await _run(
        _LLM([_text("The answer begins and", FinishReason.LENGTH)])
    )
    assert kind == RunLogKind.RUN_COMPLETED
    assert RunLogKind.RUN_TRUNCATED in kinds, (
        "a truncated reply looked exactly like a finished one"
    )


async def test_i21_a_finished_reply_is_not_marked_truncated() -> None:
    _, _, kinds = await _run(_LLM([_text("All done.")]))
    assert RunLogKind.RUN_TRUNCATED not in kinds


async def test_i21_a_content_filter_stop_fails_the_run_with_its_own_code() -> None:
    kind, payload, _ = await _run(
        _LLM([_text("", FinishReason.CONTENT_FILTER)]), retries=3
    )
    assert kind == RunLogKind.RUN_FAILED and payload["status"] == "content_filter"


async def test_i21_a_context_overflow_is_retried_once_with_a_smaller_prompt() -> None:
    from substrate.types import ToolUseBlock
    from substrate.tools import Toolbox
    from tests.invariants._harness.scenarios import ChargeCard

    tools = Toolbox()
    tools.add(ChargeCard([]))
    # Turn 1 calls a tool, so the second call carries [user, assistant(tool call), tool result].
    llm = _LLM(
        [
            (
                [
                    ToolUseBlock(
                        call_id="c1", tool_name="charge_card", arguments={"amount": 1}
                    )
                ],
                FinishReason.TOOL_CALLS,
            ),
            ContextLengthError("too long"),
            _text("fits now"),
        ]
    )
    kind, _, _ = await _run_with(
        ReActAgent("bot", model=llm, tools=tools, max_iterations=4)
    )
    assert kind == RunLogKind.RUN_COMPLETED
    assert len(llm.seen) == 3, "the overflow was not retried"
    assert llm.seen[2] < llm.seen[1], (
        f"the retry did not send a smaller prompt: {llm.seen}"
    )


async def test_i21_a_second_overflow_is_real_and_fails_the_run() -> None:
    kind, payload, _ = await _run(
        _LLM([ContextLengthError("too long"), ContextLengthError("still too long")]),
        retries=3,
    )
    assert kind == RunLogKind.RUN_FAILED and payload["status"] == "context_length"


async def test_i21_a_rate_limit_is_retried_not_failed() -> None:
    kind, _, kinds = await _run(
        _LLM([RateLimitedError("slow", retry_after=0.0), _text("ok")]), retries=2
    )
    assert kind == RunLogKind.RUN_COMPLETED and RunLogKind.RUN_RETRYING in kinds

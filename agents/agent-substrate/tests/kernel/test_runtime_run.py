"""Runtime.run() — the ergonomic one-shot API returns the final answer."""

from __future__ import annotations

from substrate.models import ModelCapabilities
from substrate.agents import ReActAgent
from substrate.types import TextBlock
from substrate.types import Usage
from substrate.types import CompletionEvent, TextDelta
from substrate.types import RunStatus
from substrate.context import ContextConfig
from substrate.testing.runtime import ephemeral_runtime
from tests._stores import fs_history


class _StubLLM:
    """Streams a fixed assistant answer (deltas + completion), no tool calls."""

    model = "stub"
    capabilities = ModelCapabilities(model_id="stub")

    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def generate_stream(self, messages, *, options, ctx=None):
        yield TextDelta(text=self._answer)
        yield CompletionEvent(content=[TextBlock(text=self._answer)], usage=Usage())


async def test_run_returns_final_text() -> None:
    agent = ReActAgent("assistant", model=_StubLLM("42 is the answer"))
    async with ephemeral_runtime() as rt:
        result = await rt.run(agent, "What is 6 times 7?")
    assert result.status == RunStatus.COMPLETED
    assert result.output == "42 is the answer"
    assert str(result) == "42 is the answer"


async def test_run_reports_failure() -> None:
    class _BoomLLM:
        model = "boom"
        capabilities = ModelCapabilities(model_id="boom")

        async def generate_stream(self, messages, *, options, ctx=None):
            raise RuntimeError("model exploded")
            yield  # pragma: no cover - makes this an async generator

    agent = ReActAgent("assistant", model=_BoomLLM())
    async with ephemeral_runtime() as rt:
        result = await rt.run(agent, "hi")
    assert result.status == RunStatus.FAILED
    assert "model exploded" in (result.error or "")


async def test_runs_on_one_thread_share_its_history_and_other_threads_do_not() -> None:
    """A thread is a conversation: the second run on it is shown the first run's turns, a run on another thread is not."""
    shown: list[int] = []

    class _CountingLLM(_StubLLM):
        async def generate_stream(self, messages, *, options, ctx=None):
            shown.append(len(messages))
            async for event in super().generate_stream(messages, options=options, ctx=ctx):
                yield event

    agent = ReActAgent("assistant", model=_CountingLLM("ok"), context=ContextConfig(fs_history()))
    async with ephemeral_runtime() as rt:
        await rt.run(agent, "first", thread="t1")
        await rt.run(agent, "second", thread="t1")
        await rt.run(agent, "elsewhere", thread="t2")
        await rt.run(agent, "no thread")

    # user | user + assistant + user | user | user  (a system prompt, when present, adds one to each)
    assert [b - a for a, b in zip(shown, shown[1:])] == [2, -2, 0]

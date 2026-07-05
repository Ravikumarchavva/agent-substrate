"""Runtime.run() — the ergonomic one-shot API returns the final answer."""

from __future__ import annotations

from substrate.agents.core.react import ReActAgent
from substrate.agents.runtime import Runtime
from substrate.kernel.core.content import TextBlock
from substrate.kernel.core.usage import Usage
from substrate.kernel.messaging.stream import CompletionEvent, TextDelta
from substrate.kernel.runtime.ids import RunStatus


class _StubLLM:
    """Streams a fixed assistant answer (deltas + completion), no tool calls."""

    model = "stub"

    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def generate_stream(self, messages, *, options, ctx=None):
        yield TextDelta(text=self._answer)
        yield CompletionEvent(content=[TextBlock(text=self._answer)], usage=Usage())


async def test_run_returns_final_text() -> None:
    agent = ReActAgent("assistant", model=_StubLLM("42 is the answer"))
    async with Runtime() as rt:
        result = await rt.run(agent, "What is 6 times 7?")
    assert result.status == RunStatus.COMPLETED
    assert result.output == "42 is the answer"
    assert str(result) == "42 is the answer"


async def test_run_reports_failure() -> None:
    class _BoomLLM:
        model = "boom"

        async def generate_stream(self, messages, *, options, ctx=None):
            raise RuntimeError("model exploded")
            yield  # pragma: no cover - makes this an async generator

    agent = ReActAgent("assistant", model=_BoomLLM())
    async with Runtime() as rt:
        result = await rt.run(agent, "hi")
    assert result.status == RunStatus.FAILED
    assert "model exploded" in (result.error or "")

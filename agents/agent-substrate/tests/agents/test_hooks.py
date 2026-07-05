"""Tests for HookManager wiring into the agent run loop.

Covers all three dispatch points added in the audit:
  - RUN_START / RUN_END  — Worker._run_agent()
  - TOOL_START / TOOL_END — ToolInvoker.invoke()
  - LLM_START / LLM_END  — ReActAgent._react_loop()
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from substrate.agents.context import (
    CompactionPipeline,
    ContextConfig,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
)
from substrate.agents.core import ReActAgent
from substrate.agents.hooks.manager import HookEvent, HookManager
from substrate.agents.runtime import Runtime
from substrate.agents.tools.invoker import ToolInvoker
from substrate.agents.tools.toolbox import Toolbox
from substrate.kernel import TextBlock, ToolExecutionResult, ToolRisk
from substrate.kernel.core.identity import AgentId
from substrate.kernel.llm import GenerationOptions, LLMResponse, Usage
from substrate.kernel.messaging.message import ChatPayload, DataPayload, Message
from substrate.kernel.messaging.stream import CompletionEvent, TextDelta
from substrate.kernel.core.content import ChatMessage, Role


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _fired(events: list[dict], event: HookEvent) -> list[dict]:
    return [e for e in events if e.get("_event") == event]


def _recording_hooks(*tracked: HookEvent) -> tuple[HookManager, list[dict]]:
    """Return a HookManager that records payloads for the given events."""
    log: list[dict] = []
    hooks = HookManager()
    for event in tracked:

        def _make_handler(ev: HookEvent):
            async def handler(ctx: dict) -> None:
                log.append({"_event": ev, **ctx})

            return handler

        hooks.register(event, _make_handler(event))
    return hooks, log


# ---------------------------------------------------------------------------
# Minimal stub agent (no LLM, just completes immediately)
# ---------------------------------------------------------------------------


class _MinimalAgent:
    def __init__(self, agent_id: AgentId, hooks: HookManager) -> None:
        self.id = agent_id
        self.hooks = hooks

    async def run(self, ctx: Any, inbox: list[Any]) -> None:
        pass  # completes immediately


# ---------------------------------------------------------------------------
# Stub LLM — returns a plain-text response, terminating the ReAct loop
# ---------------------------------------------------------------------------


class _StubLLM:
    model = "stub"

    def __init__(self, text: str = "done") -> None:
        self._text = text

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        return LLMResponse(content=[TextBlock(text=self._text)], usage=Usage())

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[TextDelta | CompletionEvent]:
        yield TextDelta(text=self._text)
        yield CompletionEvent(content=[TextBlock(text=self._text)], usage=Usage())

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return 0


# ---------------------------------------------------------------------------
# Stub tool
# ---------------------------------------------------------------------------


class _PingTool:
    name = "ping"
    description = "Returns pong."
    risk = ToolRisk.SAFE
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text="pong")])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_run_start_end_fire() -> None:
    """RUN_START and RUN_END are dispatched around every agent.run() call."""
    hooks, log = _recording_hooks(HookEvent.RUN_START, HookEvent.RUN_END)
    agent_id = AgentId(type="minimal", key="test")
    agent = _MinimalAgent(agent_id, hooks)

    async with Runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(
            agent_id, Message(target=agent_id, payload=DataPayload(data={}))
        )
        # Wait for the run to complete
        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                break

    starts = _fired(log, HookEvent.RUN_START)
    ends = _fired(log, HookEvent.RUN_END)
    assert len(starts) == 1, f"Expected 1 RUN_START, got {len(starts)}"
    assert len(ends) == 1, f"Expected 1 RUN_END, got {len(ends)}"
    assert starts[0]["run_id"] == run_id
    assert ends[0]["run_id"] == run_id


async def test_run_end_fires_even_on_agent_crash() -> None:
    """RUN_END fires in the finally block even when agent.run() raises."""

    class _CrashingAgent:
        id = AgentId(type="crasher", key="test")

        def __init__(self, hooks: HookManager) -> None:
            self.hooks = hooks

        async def run(self, ctx: Any, inbox: list[Any]) -> None:
            raise RuntimeError("boom")

    hooks, log = _recording_hooks(HookEvent.RUN_START, HookEvent.RUN_END)
    agent = _CrashingAgent(hooks)

    async with Runtime() as rt:
        await rt.register(agent)
        # max_retries=0: this test is about hook firing on a crash, not
        # retry semantics — a default retry policy would back the run off
        # and retry rather than terminal-failing on the first attempt.
        run_id = await rt.submit(
            agent.id,
            Message(target=agent.id, payload=DataPayload(data={})),
            max_retries=0,
        )
        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                break

    assert len(_fired(log, HookEvent.RUN_START)) == 1
    assert len(_fired(log, HookEvent.RUN_END)) == 1, "RUN_END must fire even on crash"


async def test_tool_start_end_fire() -> None:
    """TOOL_START and TOOL_END are dispatched by ToolInvoker around every tool call."""
    from substrate.kernel.tools import ToolCallRequest

    hooks, log = _recording_hooks(HookEvent.TOOL_START, HookEvent.TOOL_END)

    toolbox = Toolbox()
    toolbox.add(_PingTool())
    invoker = ToolInvoker(registry=toolbox, hooks=hooks)

    req = ToolCallRequest(call_id="c1", name="ping", arguments={})
    session = invoker.open_session()
    await invoker.invoke(req, session=session)

    starts = _fired(log, HookEvent.TOOL_START)
    ends = _fired(log, HookEvent.TOOL_END)
    assert len(starts) == 1
    assert starts[0]["tool_name"] == "ping"
    assert len(ends) == 1
    assert ends[0]["tool_name"] == "ping"
    assert "duration_ms" in ends[0]


async def test_llm_start_end_fire() -> None:
    """LLM_START and LLM_END are dispatched by ReActAgent around each LLM call."""
    hooks, log = _recording_hooks(HookEvent.LLM_START, HookEvent.LLM_END)

    agent = ReActAgent(
        "llm-hook-test",
        model=_StubLLM("done"),
        context=ContextConfig(
            InMemoryHistoryProvider(),
            pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=10)]),
        ),
        hooks=hooks,
    )
    agent_id = agent.id

    async with Runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(
            agent_id,
            Message(
                target=agent_id,
                payload=ChatPayload(
                    message=ChatMessage(role=Role.USER, content=[TextBlock(text="hi")])
                ),
            ),
        )
        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                break

    assert len(_fired(log, HookEvent.LLM_START)) >= 1, "LLM_START must fire"
    assert len(_fired(log, HookEvent.LLM_END)) >= 1, "LLM_END must fire"

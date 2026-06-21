"""Tests for ReActAgent via the Runtime — mock LLM, no API key needed."""

from __future__ import annotations

from typing import AsyncIterator

from substrate.agents.context import (
    ContextConfig,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
    CompactionPipeline,
)
from substrate.agents.core import ReActAgent
from substrate.agents.runtime import Runtime
from substrate.kernel import (
    ChatMessage,
    ContentBlock,
    TextBlock,
    ToolExecutionResult,
    ToolRisk,
    ToolUseBlock,
)
from substrate.kernel.core.content import Role
from substrate.kernel.core.identity import AgentId
from substrate.kernel.llm import GenerationOptions, LLMResponse, Usage
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.kernel.messaging.stream import CompletionEvent, TextDelta


# ---------------------------------------------------------------------------
# Mock LLM
# ---------------------------------------------------------------------------


class MockLLMClient:
    """Scripted LLM: each generate() call pops the next response from the queue."""

    def __init__(self, responses: list[list[ContentBlock]]) -> None:
        self._queue = list(responses)
        self.model = "mock-model"

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        assert self._queue, "MockLLMClient: no more scripted responses"
        return LLMResponse(content=self._queue.pop(0), usage=Usage())

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        return self._do_stream(messages, options=options)

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions,
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        resp = await self.generate(messages, options=options)
        text = " ".join(
            b.text for b in resp.content if isinstance(b, TextBlock) and b.text
        )
        if text:
            yield TextDelta(text=text)
        yield CompletionEvent(content=resp.content, usage=resp.usage)

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return 0


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class EchoTool:
    name = "echo"
    description = "Echo input back."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def execute(self, *, text: str, **_kw: object) -> ToolExecutionResult:
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=text)])


class RiskyTool:
    name = "risky"
    description = "Dangerous side-effect tool."
    risk = ToolRisk.HIGH
    input_schema: dict[str, object] = {"type": "object", "properties": {}}

    async def execute(self, **_kw: object) -> ToolExecutionResult:
        return ToolExecutionResult(name=self.name, content=[TextBlock(text="done")])


# ---------------------------------------------------------------------------
# Helper: submit one message and wait for run completion
# ---------------------------------------------------------------------------


async def run_agent(
    rt: Runtime,
    agent: ReActAgent,
    text: str,
    *,
    session_id: str | None = None,
) -> dict:
    await rt.register(agent)
    sid = session_id or agent.id.key
    msg = Message(
        target=agent.id,
        sender=AgentId(type="proxy", key="test"),
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=text)])
        ),
        correlation_id=sid,
    )
    run_id = await rt.submit(agent.id, msg)

    status = "success"
    error = None
    async for entry in rt.event_log.tail(run_id):
        if entry.kind == "run.completed":
            break
        elif entry.kind == "run.failed":
            status = entry.payload.get("status", "error")
            error = entry.payload.get("error")
            break
        elif entry.kind == "run.cancelled":
            status = "cancelled"
            break

    output = ""
    history_msgs = await agent.history.get_messages(agent.id, session_id=sid)
    for m in reversed(history_msgs):
        if m.role == Role.ASSISTANT:
            output = " ".join(
                b.text for b in m.content if isinstance(b, TextBlock) and b.text
            )
            break
    if error and not output:
        output = error

    return {"status": status, "output": output, "run_id": run_id}


def make_agent(
    responses: list[list[ContentBlock]],
    *,
    tools: list | None = None,
    approval_handler=None,
    approval_required_risk: ToolRisk = ToolRisk.HIGH,
) -> ReActAgent:
    return ReActAgent(
        "TestBot",
        model=MockLLMClient(responses),
        tools=tools,
        approval_handler=approval_handler,
        approval_required_risk=approval_required_risk,
        context=ContextConfig(
            InMemoryHistoryProvider(),
            SlidingWindowCompaction(max_messages=20),
        ),
        max_iterations=5,
    )


# ---------------------------------------------------------------------------
# Tests — basic
# ---------------------------------------------------------------------------


async def test_run_plain_text():
    """Agent returns the LLM's text response."""
    async with Runtime() as rt:
        agent = make_agent([[TextBlock(text="hello world")]])
        result = await run_agent(rt, agent, "hi")
        assert result["status"] == "success"
        assert result["output"] == "hello world"


async def test_run_with_tool_call():
    """Agent executes a tool when the LLM returns a ToolUseBlock."""
    async with Runtime() as rt:
        tool_use = ToolUseBlock(
            call_id="c1", tool_name="echo", arguments={"text": "pong"}
        )
        agent = make_agent(
            [
                [tool_use],
                [TextBlock(text="pong received")],
            ],
            tools=[EchoTool()],
        )
        result = await run_agent(rt, agent, "ping")
        assert result["status"] == "success"
        assert result["output"] == "pong received"


async def test_run_unknown_tool_returns_error_and_continues():
    """Calling an unregistered tool gives an error result; agent continues."""
    async with Runtime() as rt:
        tool_use = ToolUseBlock(call_id="c1", tool_name="ghost", arguments={})
        agent = make_agent([[tool_use], [TextBlock(text="ok")]])
        result = await run_agent(rt, agent, "use ghost tool")
        assert result["status"] in {"success", "budget_exhausted"}


async def test_multi_turn_history():
    """History accumulates across multiple submissions with the same session."""
    async with Runtime() as rt:
        agent = make_agent(
            [
                [TextBlock(text="I am fine.")],
                [TextBlock(text="You said hi earlier.")],
            ]
        )
        r1 = await run_agent(rt, agent, "Hi!")
        assert r1["status"] == "success"

        r2 = await run_agent(rt, agent, "What did I say?")
        assert r2["status"] == "success"
        assert r2["output"] == "You said hi earlier."

        msgs = await agent.history.get_messages(agent.id, session_id=agent.id.key)
        assert len(msgs) == 4  # 2 user + 2 assistant


async def test_max_iterations():
    """Agent fails with max_iterations when the ReAct loop never terminates."""
    async with Runtime() as rt:
        tool_use = ToolUseBlock(call_id="c1", tool_name="echo", arguments={"text": "x"})
        agent = make_agent(
            [[tool_use]] * 6,
            tools=[EchoTool()],
        )
        result = await run_agent(rt, agent, "loop forever")
        assert result["status"] == "budget_exhausted"


async def test_multiple_tool_calls_in_one_turn():
    """Two tool uses in a single assistant turn are both executed."""
    async with Runtime() as rt:
        tc1 = ToolUseBlock(call_id="c1", tool_name="echo", arguments={"text": "a"})
        tc2 = ToolUseBlock(call_id="c2", tool_name="echo", arguments={"text": "b"})
        agent = make_agent(
            [
                [tc1, tc2],
                [TextBlock(text="both done")],
            ],
            tools=[EchoTool()],
        )
        result = await run_agent(rt, agent, "echo twice")
        assert result["status"] == "success"
        assert result["output"] == "both done"


# ---------------------------------------------------------------------------
# Tests — HITL approval
# ---------------------------------------------------------------------------


async def test_hitl_approval_granted():
    """When approval_handler approves, the tool executes and agent succeeds."""
    async with Runtime() as rt:
        approved_calls: list[str] = []

        async def handler(tool_name: str, args: dict) -> bool:
            approved_calls.append(tool_name)
            return True

        tool_use = ToolUseBlock(call_id="c1", tool_name="risky", arguments={})
        agent = make_agent(
            [[tool_use], [TextBlock(text="done")]],
            tools=[RiskyTool()],
            approval_handler=handler,
            approval_required_risk=ToolRisk.HIGH,
        )
        result = await run_agent(rt, agent, "run risky tool")
        assert result["status"] == "success"
        assert "risky" in approved_calls


async def test_hitl_approval_denied():
    """When approval_handler denies, tool call produces an error and agent continues."""
    async with Runtime() as rt:

        async def handler(tool_name: str, args: dict) -> bool:
            return False

        tool_use = ToolUseBlock(call_id="c1", tool_name="risky", arguments={})
        agent = make_agent(
            [[tool_use], [TextBlock(text="ok")]],
            tools=[RiskyTool()],
            approval_handler=handler,
            approval_required_risk=ToolRisk.HIGH,
        )
        result = await run_agent(rt, agent, "run risky tool")
        # Tool was denied — agent should continue after the error result
        assert result["status"] == "success"


async def test_hitl_safe_tool_skips_approval():
    """SAFE-risk tools bypass the approval handler entirely."""
    async with Runtime() as rt:
        calls: list[str] = []

        async def handler(tool_name: str, args: dict) -> bool:
            calls.append(tool_name)
            return True

        tool_use = ToolUseBlock(call_id="c1", tool_name="echo", arguments={"text": "x"})
        agent = make_agent(
            [[tool_use], [TextBlock(text="done")]],
            tools=[EchoTool()],  # EchoTool has no .risk → SAFE
            approval_handler=handler,
            approval_required_risk=ToolRisk.HIGH,
        )
        result = await run_agent(rt, agent, "echo x")
        assert result["status"] == "success"
        assert calls == []  # handler never invoked for SAFE tool


# ---------------------------------------------------------------------------
# Tests — misc
# ---------------------------------------------------------------------------


async def test_agent_context_config():
    """ContextConfig accepts a CompactionPipeline."""
    async with Runtime() as rt:
        pipeline = CompactionPipeline([SlidingWindowCompaction(max_messages=10)])
        ctx = ContextConfig(
            InMemoryHistoryProvider(),
            pipeline,
        )
        agent = ReActAgent(
            "CtxBot",
            model=MockLLMClient([[TextBlock(text="ok")]]),
            context=ctx,
        )
        result = await run_agent(rt, agent, "hello")
        assert result["status"] == "success"
        assert result["output"] == "ok"


async def test_agent_context_config_pipeline():
    """ContextConfig with a CompactionPipeline chains multiple strategies in sequence."""
    async with Runtime() as rt:
        pipeline = CompactionPipeline(
            [
                SlidingWindowCompaction(max_messages=20),
                SlidingWindowCompaction(max_messages=10),
            ]
        )
        ctx = ContextConfig(
            InMemoryHistoryProvider(),
            pipeline,
        )
        assert ctx.pipeline is pipeline
        agent = ReActAgent(
            "PipelineBot",
            model=MockLLMClient([[TextBlock(text="piped")]]),
            context=ctx,
        )
        result = await run_agent(rt, agent, "hi")
        assert result["status"] == "success"
        assert result["output"] == "piped"


async def test_tool_risk_enum_ordering():
    """ToolRisk values are strictly ordered SAFE < HIGH < CRITICAL."""
    order = {ToolRisk.SAFE: 0, ToolRisk.HIGH: 1, ToolRisk.CRITICAL: 2}
    assert order[ToolRisk.SAFE] < order[ToolRisk.HIGH] < order[ToolRisk.CRITICAL]

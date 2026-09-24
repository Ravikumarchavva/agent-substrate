"""ReActAgent harness behavior: concurrent tool batches (replay-safe), cost
budgets, bad tool-call arguments, and graceful step-limit handling."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from substrate.agents.context import ContextConfig
from substrate.agents.core.react import ReActAgent
from substrate.agents.limits.execution import ExecutionTracker
from substrate.agents.runtime.runtime import Runtime
from substrate.agents.storage.history import InMemoryHistoryProvider
from substrate.kernel.core.content import (
    ChatMessage,
    ContentBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from substrate.kernel.core.identity import Actor
from substrate.kernel.core.usage import Usage
from substrate.kernel.llm import GenerationOptions, ModelCapabilities
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.kernel.messaging.stream import CompletionEvent
from substrate.kernel.runtime.scheduler import RunRetryPolicy
from substrate.kernel.tools import ToolExecutionResult


class ScriptedLLM:
    """Plays back one scripted turn per call; a turn may be an Exception to raise."""

    def __init__(
        self,
        turns: list[list[ContentBlock] | Exception],
        *,
        capabilities: ModelCapabilities | None = None,
        usage: Usage | None = None,
    ) -> None:
        self.model = "scripted"
        self.capabilities = capabilities or ModelCapabilities(model_id="scripted")
        self._turns = list(turns)
        self._usage = usage or Usage()
        self.calls = 0
        self.seen: list[list[ChatMessage]] = []

    async def generate(self, messages, *, options=GenerationOptions(), ctx=None):  # noqa: B008
        raise NotImplementedError

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
        ctx: object = None,
    ) -> AsyncIterator[CompletionEvent]:
        return self._stream(messages)

    async def _stream(self, messages: list[ChatMessage]) -> AsyncIterator[CompletionEvent]:
        self.calls += 1
        self.seen.append(list(messages))
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        yield CompletionEvent(content=turn, usage=self._usage)

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return 0


class PairTool:
    """One tool called twice per batch (``who="a"`` / ``who="b"``): each call
    finishes only once the other has *started*, so it can only succeed when
    both run at the same time. Same tool name for both calls on purpose —
    their journal identity then comes from their path alone."""

    name = "pair"
    description = "test tool"
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"who": {"type": "string"}},
        "required": ["who"],
    }
    concurrency_safe = True

    def __init__(self, wait_s: float = 2.0) -> None:
        self._started = {"a": asyncio.Event(), "b": asyncio.Event()}
        self._wait_s = wait_s
        self.executions: list[str] = []

    async def execute(self, *, who: str, **_kw: object) -> ToolExecutionResult:
        self.executions.append(who)
        self._started[who].set()
        other = "b" if who == "a" else "a"
        await asyncio.wait_for(self._started[other].wait(), timeout=self._wait_s)
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=f"{who} ok")])


class CountingTool:
    name = "count"
    description = "counts executions"
    input_schema: dict[str, object] = {"type": "object", "properties": {"x": {"type": "integer"}}}

    def __init__(self) -> None:
        self.executions = 0

    async def execute(self, **_kw: object) -> ToolExecutionResult:
        self.executions += 1
        return ToolExecutionResult(name=self.name, content=[TextBlock(text="counted")])


async def run_to_end(
    rt: Runtime, agent: ReActAgent, *, retry_policy=None
) -> tuple[str, dict, str]:
    await rt.register(agent)
    msg = Message(
        target=agent.id,
        sender=Actor(type="proxy", key="user"),
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text="go")])
        ),
        correlation_id="s1",
    )
    kwargs = {"retry_policy": retry_policy} if retry_policy else {}
    run_id = await rt.submit(agent.id, msg, **kwargs)
    async for entry in rt.event_log.tail(run_id):
        if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
            return entry.kind, entry.payload, run_id
    raise AssertionError("log ended without a terminal entry")


def make_agent(llm: ScriptedLLM, **kwargs: object) -> ReActAgent:
    """Isolated history per agent — the default is an on-disk store shared by session id."""
    return ReActAgent(
        "bot",
        model=llm,
        context=ContextConfig(history=InMemoryHistoryProvider()),
        **kwargs,  # type: ignore[arg-type]
    )


def tool_result_texts(messages: list[ChatMessage]) -> list[str]:
    return [
        b.text
        for m in messages
        if m.role == Role.TOOL
        for b in m.content
        if isinstance(b, ToolResultBlock)
    ]


def _use(name: str, call_id: str, **args: object) -> ToolUseBlock:
    return ToolUseBlock(call_id=call_id, tool_name=name, arguments=dict(args))


async def _tool_results(rt: Runtime, run_id: str) -> list[dict]:
    return [
        e.payload
        async for e in rt.event_log.read(run_id, from_seq=0)
        if e.kind == "tool.result"
    ]


async def test_concurrency_safe_calls_run_together_and_keep_call_order():
    tool = PairTool()
    llm = ScriptedLLM(
        [[_use("pair", "c1", who="a"), _use("pair", "c2", who="b")], [TextBlock(text="done")]]
    )
    agent = make_agent(llm, tools=[tool])

    async with Runtime() as rt:
        kind, _, run_id = await run_to_end(rt, agent)
        results = await _tool_results(rt, run_id)

    assert kind == "run.completed"
    assert [r["output"] for r in results] == ["a ok", "b ok"]
    assert all(r["ok"] for r in results)


async def test_calls_without_the_marker_run_one_at_a_time():
    tool = PairTool(wait_s=0.2)
    tool.concurrency_safe = False
    llm = ScriptedLLM(
        [[_use("pair", "c1", who="a"), _use("pair", "c2", who="b")], [TextBlock(text="done")]]
    )
    agent = make_agent(llm, tools=[tool])

    async with Runtime() as rt:
        _, _, run_id = await run_to_end(rt, agent)
        results = await _tool_results(rt, run_id)

    # One after another: "a" waits for a partner that hasn't started, times
    # out and reports an error; "b" then finds "a" already started.
    assert [r["ok"] for r in results] == [False, True]


async def test_replay_after_a_crash_finds_each_batched_call_at_its_own_path():
    """Two calls to the same tool are told apart only by their journal path.
    After a later failure the run is retried: each must be found again at the
    same path — not re-executed, and not handed the other call's result."""
    tool = PairTool()
    llm = ScriptedLLM(
        [
            [_use("pair", "c1", who="a"), _use("pair", "c2", who="b")],
            RuntimeError("provider blew up"),
            [_use("pair", "c3", who="a"), _use("pair", "c4", who="b")],
            [TextBlock(text="done")],
        ]
    )
    agent = make_agent(llm, tools=[tool])

    async with Runtime() as rt:
        kind, _, run_id = await run_to_end(
            rt, agent, retry_policy=RunRetryPolicy(max_retries=2, backoff_s=0.0)
        )
        results = await _tool_results(rt, run_id)

    assert kind == "run.completed"
    assert sorted(tool.executions) == ["a", "a", "b", "b"]  # 2 batches, nothing re-run
    assert [r["output"] for r in results] == ["a ok", "b ok", "a ok", "b ok"]
    # What the model was shown after the retry: batch 1 rebuilt from the
    # journal, each call getting its own result (a cache hit logs nothing, so
    # only this reveals a path mix-up).
    assert tool_result_texts(llm.seen[2]) == ["a ok", "b ok"]


async def test_cost_budget_stops_the_run():
    caps = ModelCapabilities(model_id="scripted", input_cost_per_mtok=1.0)
    llm = ScriptedLLM(
        [[_use("count", "c1")], [_use("count", "c2")], [TextBlock(text="never")]],
        capabilities=caps,
        usage=Usage(input_tokens=1_000_000),  # $1.00 per call
    )
    agent = make_agent(
        llm, tools=[CountingTool()], execution_budget=ExecutionTracker(max_cost_usd=1.5)
    )
    async with Runtime() as rt:
        kind, payload, _ = await run_to_end(rt, agent)

    assert kind == "run.failed"
    assert payload["status"] == "budget_exhausted"
    assert llm.calls == 2


async def test_invalid_tool_arguments_are_reported_to_the_model_not_executed():
    tool = CountingTool()
    bad = ToolUseBlock(
        call_id="c1", tool_name="count", arguments_error="arguments were not valid JSON"
    )
    llm = ScriptedLLM([[bad], [_use("count", "c2", x=1)], [TextBlock(text="done")]])
    agent = make_agent(llm, tools=[tool])

    async with Runtime() as rt:
        kind, _, _ = await run_to_end(rt, agent)

    assert kind == "run.completed"
    assert tool.executions == 1  # only the retry with valid arguments ran
    assert llm.calls == 3


class _ProviderError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


async def test_a_request_that_can_never_succeed_fails_immediately_without_retries():
    llm = ScriptedLLM([_ProviderError(401), [TextBlock(text="never")]])
    agent = make_agent(llm)
    async with Runtime() as rt:
        kind, payload, _ = await run_to_end(
            rt, agent, retry_policy=RunRetryPolicy(max_retries=3, backoff_s=0.0)
        )

    assert kind == "run.failed"
    assert payload["status"] == "permanent_error"
    assert llm.calls == 1  # not retried


async def test_a_transient_provider_error_is_still_retried():
    llm = ScriptedLLM([_ProviderError(503), [TextBlock(text="recovered")]])
    agent = make_agent(llm)
    async with Runtime() as rt:
        kind, _, _ = await run_to_end(
            rt, agent, retry_policy=RunRetryPolicy(max_retries=3, backoff_s=0.0)
        )

    assert kind == "run.completed"
    assert llm.calls == 2


async def test_every_tool_returned_image_reaches_the_model_not_only_the_inline_ones():
    """Regression: an image referenced by URL or file_id (no bytes) used to be
    dropped between the tool and the model, and every image left a
    "[Image: ...]" placeholder in the text on top of the real thing."""
    from substrate.kernel.core.content import MediaBlock

    class GalleryTool:
        name = "gallery"
        description = "returns three images"
        input_schema: dict[str, object] = {"type": "object", "properties": {}}

        async def execute(self, **_kw: object) -> ToolExecutionResult:
            return ToolExecutionResult(
                name=self.name,
                content=[
                    TextBlock(text="three pictures"),
                    MediaBlock.image(data=b"\x89PNG", media_type="image/png"),
                    MediaBlock.image(url="https://example.com/a.png"),
                    MediaBlock.image(file_id="file-abc"),
                ],
            )

    llm = ScriptedLLM([[_use("gallery", "c1")], [TextBlock(text="done")]])
    agent = make_agent(llm, tools=[GalleryTool()])
    async with Runtime() as rt:
        await run_to_end(rt, agent)

    (result,) = [
        b
        for m in llm.seen[1]
        if m.role == Role.TOOL
        for b in m.content
        if isinstance(b, ToolResultBlock)
    ]
    media = [b for b in result.content if isinstance(b, MediaBlock)]
    assert [(m.url, m.file_id, m.data is not None) for m in media] == [
        (None, None, True),
        ("https://example.com/a.png", None, False),
        (None, "file-abc", False),
    ]
    assert result.text == "three pictures"  # no "[Image: ...]" placeholders


async def test_the_reply_is_the_answer_text_not_the_reasoning_trace():
    """Regression: the final answer used to be the last assistant turn
    stringified whole, so a reasoning block came back as "[Reasoning] ..."."""
    from substrate.agents.middleware.pipeline import MiddlewarePipeline
    from substrate.kernel.agent.middleware import MiddlewareStage
    from substrate.kernel.core.content import ReasoningBlock

    outputs: list[str] = []

    class CaptureOutput:
        stages = frozenset({MiddlewareStage.TURN})

        async def process(self, context, call_next) -> None:
            await call_next()
            outputs.append(context.turn_result.output)

    llm = ScriptedLLM(
        [[ReasoningBlock(text="private chain of thought"), TextBlock(text="The answer is 4.")]]
    )
    agent = make_agent(llm, middleware=MiddlewarePipeline([CaptureOutput()]))
    async with Runtime() as rt:
        await run_to_end(rt, agent)

    assert outputs == ["The answer is 4."]


async def test_a_budget_stop_keeps_the_turn_in_history():
    """Regression: a budget error mid-turn skipped persistence, so the user's
    message and every tool result vanished from the conversation."""
    from substrate.agents.storage.history import project_messages

    history = InMemoryHistoryProvider()
    llm = ScriptedLLM(
        [[_use("count", "c1")], [_use("count", "c2")]],
        usage=Usage(input_tokens=10),
    )
    agent = ReActAgent(
        "bot",
        model=llm,
        tools=[CountingTool()],
        context=ContextConfig(history=history),
        execution_budget=ExecutionTracker(max_turns=1),
    )
    async with Runtime() as rt:
        kind, payload, _ = await run_to_end(rt, agent)
        saved = await project_messages(history, "s1")

    assert kind == "run.failed" and payload["status"] == "budget_exhausted"
    roles = [m.role for m in saved]
    assert roles == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    assert "Stopped before finishing" in saved[-1].text


async def test_an_agent_can_make_more_than_fifty_tool_calls_in_one_run():
    """Regression: the sandbox-chain policy (50 calls, 60 s) governed every
    direct tool call, so call 51 came back as "Chain budget exhausted"."""
    tool = CountingTool()
    turns: list = [[_use("count", f"c{i}")] for i in range(60)] + [[TextBlock(text="done")]]
    agent = make_agent(ScriptedLLM(turns), tools=[tool], max_iterations=70)

    async with Runtime() as rt:
        kind, _, run_id = await run_to_end(rt, agent)
        results = await _tool_results(rt, run_id)

    assert kind == "run.completed"
    assert tool.executions == 60
    assert all(r["ok"] for r in results)


async def test_direct_tool_calls_are_not_cut_off_at_the_chain_default_of_60s():
    from substrate.agents.tools.invoker import DIRECT_CALL_POLICY

    assert DIRECT_CALL_POLICY.call_timeout_s >= 300  # the code interpreter's own max


async def test_an_agent_can_set_its_own_tool_policy():
    from substrate.kernel.tools.chain import ChainPolicy

    class SlowTool:
        name = "slow"
        description = "sleeps"
        input_schema: dict[str, object] = {"type": "object", "properties": {}}

        async def execute(self, **_kw: object) -> ToolExecutionResult:
            await asyncio.sleep(0.5)
            return ToolExecutionResult(name=self.name, content=[TextBlock(text="late")])

    llm = ScriptedLLM([[_use("slow", "c1")], [TextBlock(text="done")]])
    agent = make_agent(llm, tools=[SlowTool()], tool_policy=ChainPolicy(call_timeout_s=0.05))

    async with Runtime() as rt:
        _, _, run_id = await run_to_end(rt, agent)
        (result,) = await _tool_results(rt, run_id)

    assert result["ok"] is False and "timed out" in result["output"]

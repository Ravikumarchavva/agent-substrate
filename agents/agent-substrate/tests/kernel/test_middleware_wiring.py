"""End-to-end tests for the real middleware dispatch points on ReActAgent.

Unlike test_middleware.py/test_guardrails.py (which call `.process()` in
isolation with hand-built contexts), these run a real ReActAgent through the
Runtime/Worker so each stage is exercised at its actual call site:

- MiddlewareStage.TURN — wraps each inbox message/turn (agents/core/react.py)
- MiddlewareStage.CHAT — wraps each ctx.llm() call (agents/runtime/context.py)
- MiddlewareStage.TOOL — wraps each ctx.tool() call (agents/runtime/context.py)

Every test wires its middleware into `agent.middleware = MiddlewarePipeline([...])`
identically regardless of which stage that middleware targets — proof that
there's genuinely one middleware concept, not three.
"""

from __future__ import annotations

from substrate.agents.middleware import (
    CacheMiddleware,
    ContentFilterMiddleware,
    MaxTokenMiddleware,
    MiddlewarePipeline,
    PIIDetectionMiddleware,
)
from substrate.agents.runtime import Runtime
from substrate.kernel import TextBlock, ToolExecutionResult, ToolUseBlock

from tests.reasoning.test_assistant_agent import make_agent, run_agent


class CountingTool:
    """Tool that records how many times its body actually executed."""

    name = "counting_tool"
    description = "Increments a call counter."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
    }

    def __init__(self) -> None:
        self.call_count = 0

    async def execute(self, *, value: str = "", **_kw: object) -> ToolExecutionResult:
        self.call_count += 1
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=value)])


async def test_turn_stage_middleware_blocks_via_content_filter():
    """A ContentFilterMiddleware (TURN stage) halts the turn before any LLM
    call happens — the run ends 'guardrail_tripped', not 'success'."""
    async with Runtime() as rt:
        agent = make_agent([[TextBlock(text="should never be reached")]])
        agent.middleware = MiddlewarePipeline(
            [ContentFilterMiddleware(blocked_keywords=["badword"])]
        )
        result = await run_agent(rt, agent, "this has a BADWORD in it")
        assert result["status"] == "guardrail_tripped"


async def test_turn_stage_middleware_passes_clean_input():
    async with Runtime() as rt:
        agent = make_agent([[TextBlock(text="all clear")]])
        agent.middleware = MiddlewarePipeline(
            [ContentFilterMiddleware(blocked_keywords=["badword"])]
        )
        result = await run_agent(rt, agent, "a perfectly clean message")
        assert result["status"] == "success"
        assert result["output"] == "all clear"


async def test_chat_stage_middleware_blocks_via_max_token():
    """MaxTokenMiddleware (CHAT stage) sees the real messages passed to
    ctx.llm() and blocks before the (mock) LLM is ever called."""
    async with Runtime() as rt:
        agent = make_agent([[TextBlock(text="should never be reached")]])
        agent.middleware = MiddlewarePipeline(
            [MaxTokenMiddleware(max_tokens=1, chars_per_token=1.0)]
        )
        result = await run_agent(rt, agent, "a" * 100)
        assert result["status"] == "guardrail_tripped"


async def test_tool_stage_middleware_blocks_via_pii_detection():
    """PIIDetectionMiddleware (TOOL stage) inspects real tool arguments and
    blocks before the tool actually executes."""
    async with Runtime() as rt:
        tool = CountingTool()
        tool_use = ToolUseBlock(
            call_id="c1",
            tool_name="counting_tool",
            arguments={"value": "contact me at person@example.com"},
        )
        agent = make_agent([[tool_use]], tools=[tool])
        agent.middleware = MiddlewarePipeline([PIIDetectionMiddleware()])
        result = await run_agent(rt, agent, "please use the tool")
        assert result["status"] == "guardrail_tripped"
        assert tool.call_count == 0


async def test_tool_stage_middleware_cache_hit_skips_real_tool_call():
    """CacheMiddleware's (TOOL stage) skip-call_next-on-hit path is honored:
    a second identical tool call doesn't re-invoke the underlying tool."""
    async with Runtime() as rt:
        tool = CountingTool()
        tool_use = ToolUseBlock(
            call_id="c1", tool_name="counting_tool", arguments={"value": "x"}
        )
        agent = make_agent(
            [
                [tool_use],
                [TextBlock(text="first done")],
            ],
            tools=[tool],
        )
        cache = CacheMiddleware()
        agent.middleware = MiddlewarePipeline([cache])
        result = await run_agent(rt, agent, "call it once")
        assert result["status"] == "success"
        assert tool.call_count == 1

        # Second run, identical tool call args, same cache instance attached.
        agent2 = make_agent(
            [
                [tool_use],
                [TextBlock(text="second done")],
            ],
            tools=[tool],
        )
        agent2.middleware = MiddlewarePipeline([cache])
        result2 = await run_agent(rt, agent2, "call it again", session_id="turn-2")
        assert result2["status"] == "success"
        # Cache hit on the identical (function_name, args) pair — the real
        # tool body never re-executes.
        assert tool.call_count == 1


async def test_one_pipeline_dispatches_all_three_stages_together():
    """A single MiddlewarePipeline holding middleware for all three stages
    dispatches each at its own real call site in one run — the core claim
    of "one middleware, no different kinds": no separate slots needed."""
    async with Runtime() as rt:
        tool = CountingTool()
        tool_use = ToolUseBlock(
            call_id="c1", tool_name="counting_tool", arguments={"value": "ok"}
        )
        agent = make_agent(
            [[tool_use], [TextBlock(text="done")]],
            tools=[tool],
        )
        agent.middleware = MiddlewarePipeline(
            [
                ContentFilterMiddleware(blocked_keywords=["badword"]),  # TURN
                MaxTokenMiddleware(max_tokens=1000),  # CHAT
                PIIDetectionMiddleware(),  # TOOL
            ]
        )
        result = await run_agent(rt, agent, "a clean request")
        assert result["status"] == "success"
        assert result["output"] == "done"
        assert tool.call_count == 1

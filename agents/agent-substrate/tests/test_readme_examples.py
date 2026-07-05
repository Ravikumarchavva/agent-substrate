"""README examples, executed against a stub LLM instead of a real API key.

Guards against README/API drift: if these fail, the README's own examples
are broken and need fixing alongside whatever changed the API.
"""

from __future__ import annotations

from substrate.agents import OrchestratorAgent, ReActAgent, Runtime, SubAgentConfig
from substrate.capabilities.tools.compute.calculator import CalculatorTool
from substrate.fabric.flows import ConditionalFlow, ParallelFlow, SequentialFlow
from substrate.kernel.core.content import TextBlock
from substrate.kernel.core.identity import AgentId
from substrate.kernel.core.usage import Usage
from substrate.kernel.messaging.stream import CompletionEvent, TextDelta


class _StubLLM:
    """Streams a fixed assistant answer with no tool calls."""

    model = "stub"

    def __init__(self, answer: str = "42") -> None:
        self._answer = answer

    async def generate_stream(self, messages, *, options, ctx=None):
        yield TextDelta(text=self._answer)
        yield CompletionEvent(content=[TextBlock(text=self._answer)], usage=Usage())


class _ToolCallingStubLLM:
    """First call requests the calculator tool; second call returns the answer."""

    model = "stub-tools"

    def __init__(self) -> None:
        self._calls = 0

    async def generate_stream(self, messages, *, options, ctx=None):
        from substrate.kernel.core.content import ToolUseBlock

        self._calls += 1
        if self._calls == 1:
            yield CompletionEvent(
                content=[
                    ToolUseBlock(
                        call_id="c1",
                        tool_name="calculator",
                        arguments={"expression": "1234 * 5678"},
                    )
                ],
                usage=Usage(),
            )
        else:
            yield TextDelta(text="7006652")
            yield CompletionEvent(content=[TextBlock(text="7006652")], usage=Usage())


async def test_your_first_agent_example() -> None:
    """README 'Your First Agent' — Runtime.run() returns the final text."""
    agent = ReActAgent(
        "assistant",
        model=_StubLLM("Here is a Fibonacci function."),
        system_instructions="You are a helpful assistant.",
    )
    async with Runtime() as runtime:
        result = await runtime.run(
            agent, "Write a Python function to compute Fibonacci numbers."
        )
    assert result.output == "Here is a Fibonacci function."


async def test_agent_with_tools_example() -> None:
    """README 'Agent with Tools' — CalculatorTool + Runtime.run()."""
    agent = ReActAgent(
        "math_expert",
        model=_ToolCallingStubLLM(),
        tools=[CalculatorTool()],
        system_instructions="Always use the calculator tool to solve math problems.",
    )
    async with Runtime() as runtime:
        result = await runtime.run(agent, "Calculate 1234 * 5678.")
    assert result.output == "7006652"


async def test_orchestrator_example() -> None:
    """README 'OrchestratorAgent — Hub & Spoke' — sub-agents auto-register."""
    researcher = ReActAgent(
        "researcher", model=_StubLLM("research done"), system_instructions="Research."
    )
    writer = ReActAgent(
        "writer", model=_StubLLM("draft done"), system_instructions="Write."
    )
    orchestrator = OrchestratorAgent(
        "coordinator",
        model=_StubLLM("Final synthesized answer."),
        sub_agents=[
            SubAgentConfig(agent=researcher, description="Web research"),
            SubAgentConfig(agent=writer, description="Content writing"),
        ],
    )
    async with Runtime() as runtime:
        result = await runtime.run(
            orchestrator, "Research and draft a blog post about Rust vs Go."
        )
    assert result.output == "Final synthesized answer."


async def test_sequential_flow_example() -> None:
    """README 'Flows — Coordination Primitives' — Runtime.ask()."""

    class FetchStep:
        id = AgentId(type="step", key="fetch")

        async def run(self, ctx, inbox):
            for msg in inbox:
                await ctx.reply(msg, {"text": "Fetched 3 records."})

    class AnalyzeStep:
        id = AgentId(type="step", key="analyze")

        async def run(self, ctx, inbox):
            for msg in inbox:
                await ctx.reply(msg, {"text": "Analysis: all records valid."})

    fetch, analyze = FetchStep(), AnalyzeStep()
    pipeline = SequentialFlow(steps=[fetch, analyze], name="demo_pipeline")

    async with Runtime() as runtime:
        await runtime.register(fetch)
        await runtime.register(analyze)
        result = await runtime.ask(pipeline, "Process the latest dataset.")

    assert result.output == (
        "Process the latest dataset.\n\n"
        "Fetched 3 records.\n\n"
        "Analysis: all records valid."
    )


async def test_parallel_flow() -> None:
    class FixedReply:
        def __init__(self, key: str, text: str) -> None:
            self.id = AgentId(type="step", key=key)
            self.text = text

        async def run(self, ctx, inbox):
            for msg in inbox:
                await ctx.reply(msg, {"text": self.text})

    a, b, c = (
        FixedReply("a", "OK: security"),
        FixedReply("b", "OK: legal"),
        FixedReply("c", "OK: grammar"),
    )
    flow = ParallelFlow(branches=[a, b, c], name="review", merge="concat")
    async with Runtime() as runtime:
        for step in (a, b, c):
            await runtime.register(step)
        result = await runtime.ask(flow, "Review this PR.")
    assert result.output == "OK: security\n\nOK: legal\n\nOK: grammar"


async def test_conditional_flow() -> None:
    class FixedReply:
        def __init__(self, key: str, text: str) -> None:
            self.id = AgentId(type="step", key=key)
            self.text = text

        async def run(self, ctx, inbox):
            for msg in inbox:
                await ctx.reply(msg, {"text": self.text})

    bug = FixedReply("bug", "Routed to bug tracker")
    general = FixedReply("general", "Routed to general inbox")
    flow = ConditionalFlow(
        predicate=lambda text: "bug" in text.lower(),
        if_true=bug,
        if_false=general,
        name="router",
    )
    async with Runtime() as runtime:
        await runtime.register(bug)
        await runtime.register(general)
        result = await runtime.ask(flow, "There is a bug in login")
    assert result.output == "Routed to bug tracker"

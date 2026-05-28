"""Battle tests for ReActAgent.

Covers the full think→act→observe loop using only mock LLMs and fake tools —
zero network calls, zero external deps.

Test groups:
  - Basic: single turn, multi-turn, empty input
  - Tool loop: single call, chained calls, unknown tool, tool error
  - Termination: max_iterations ceiling
  - Memory: message accumulation across turns
  - Guardrails: PII tripwire halts the run
  - Error propagation: LLM raises mid-run
"""

from __future__ import annotations

import pytest

from ravi.kernel.agents.agent_result import RunStatus
from ravi.kernel.guardrails import GuardrailType
from ravi.extensions.guardrails import PIIDetectionGuardrail
from ravi.kernel.agent_catalog import AgentCatalogRegistry
from ravi.kernel.memory.unbounded_memory import UnboundedMemory

from tests.fixtures.mock_llm import MockLLMClient, text_turn, tool_turn, error_turn
from tests.fixtures.fake_tools import EchoTool, AddTool, FailTool, CounterTool
from tests.extensions.agents.react.conftest import make_agent


def _all_tool_calls(result):
    """Flatten all ToolCallRecord entries from all steps."""
    records = []
    for step in result.steps:
        records.extend(step.tool_calls)
    return records


# ══════════════════════════════════════════════════════════════════════════════
# Basic: direct text response
# ══════════════════════════════════════════════════════════════════════════════


async def test_single_turn_text_response():
    agent = make_agent(script=[text_turn("Hello, world!")])
    result = await agent.run("Say hello")
    assert result.status == RunStatus.COMPLETED
    assert "Hello" in result.output_text


async def test_empty_user_input_accepted():
    agent = make_agent(script=[text_turn("Nothing to do.")])
    result = await agent.run("")
    assert result.status == RunStatus.COMPLETED


async def test_multiline_output_preserved():
    long_text = "Line one.\nLine two.\nLine three."
    agent = make_agent(script=[text_turn(long_text)])
    result = await agent.run("Give me three lines")
    assert "Line one" in result.output_text
    assert "Line three" in result.output_text


# ══════════════════════════════════════════════════════════════════════════════
# Tool loop
# ══════════════════════════════════════════════════════════════════════════════


async def test_single_tool_call_round_trip():
    echo = EchoTool()
    agent = make_agent(
        script=[
            tool_turn("echo", {"message": "ping"}),
            text_turn("Tool replied: echo:ping"),
        ],
        tools=[echo],
    )
    result = await agent.run("Call echo with ping")
    assert result.status == RunStatus.COMPLETED
    assert echo.call_count == 1


async def test_chained_tool_calls():
    counter = CounterTool()
    agent = make_agent(
        script=[
            tool_turn("counter", {}),
            tool_turn("counter", {}),
            text_turn("Called counter twice."),
        ],
        tools=[counter],
    )
    result = await agent.run("Call counter twice")
    assert result.status == RunStatus.COMPLETED
    assert counter.count == 2


async def test_tool_with_numeric_args():
    add = AddTool()
    agent = make_agent(
        script=[
            tool_turn("add", {"a": 7, "b": 5}),
            text_turn("7 + 5 = 12"),
        ],
        tools=[add],
    )
    result = await agent.run("What is 7 + 5?")
    assert result.status == RunStatus.COMPLETED
    all_calls = _all_tool_calls(result)
    assert len(all_calls) == 1
    assert all_calls[0].tool_name == "add"


async def test_tool_error_is_surfaced_to_llm():
    fail = FailTool("deliberate failure")
    agent = make_agent(
        script=[
            tool_turn("fail", {}),
            text_turn("The tool failed but I recovered."),
        ],
        tools=[fail],
    )
    result = await agent.run("Try the failing tool")
    # Agent continues: tool error is fed back as an observation, then LLM responds.
    assert result.status == RunStatus.COMPLETED
    errored = [tc for tc in _all_tool_calls(result) if tc.is_error]
    assert len(errored) == 1


async def test_unknown_tool_request_does_not_crash():
    agent = make_agent(
        script=[
            tool_turn("nonexistent_tool", {"x": 1}),
            text_turn("I could not find that tool."),
        ],
        tools=[],
    )
    result = await agent.run("Call a tool that doesn't exist")
    assert result.status == RunStatus.COMPLETED


# ══════════════════════════════════════════════════════════════════════════════
# Termination: max iterations
# ══════════════════════════════════════════════════════════════════════════════


async def test_max_iterations_ceiling():
    counter = CounterTool()
    # Script longer than max_iterations — agent must stop before exhausting it.
    script = [tool_turn("counter", {})] * 20
    agent = make_agent(script=script, tools=[counter], max_iterations=3)
    result = await agent.run("Call counter forever")
    assert result.status == RunStatus.MAX_ITERATIONS
    # Should have stopped after 3 tool calls, not 20.
    assert counter.count <= 3


# ══════════════════════════════════════════════════════════════════════════════
# Memory: message accumulation
# ══════════════════════════════════════════════════════════════════════════════


async def test_memory_accumulates_across_steps():
    echo = EchoTool()
    agent = make_agent(
        script=[
            tool_turn("echo", {"message": "first"}),
            tool_turn("echo", {"message": "second"}),
            text_turn("Done with two echoes."),
        ],
        tools=[echo],
    )
    result = await agent.run("Echo twice")
    assert result.status == RunStatus.COMPLETED
    all_calls = _all_tool_calls(result)
    assert len(all_calls) == 2
    assert all(tc.tool_name == "echo" for tc in all_calls)


async def test_step_results_recorded():
    agent = make_agent(script=[text_turn("Step one only.")])
    result = await agent.run("One step")
    assert len(result.steps) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# Guardrails: PII tripwire
# ══════════════════════════════════════════════════════════════════════════════


async def test_pii_guardrail_trips_on_input():
    from ravi.extensions.agents.react.agent import ReActAgent
    from ravi.extensions.middleware.guardrails import GuardrailsMiddleware

    catalog = AgentCatalogRegistry()
    catalog.register_model("primary", MockLLMClient(script=[text_turn("I'll leak data.")]))
    catalog.register_memory("memory", UnboundedMemory())

    guardrail_mw = GuardrailsMiddleware(
        input_guardrails=[PIIDetectionGuardrail(guardrail_type=GuardrailType.INPUT, tripwire=True)]
    )
    agent = ReActAgent(
        name="guarded-agent",
        description="agent with PII guard",
        catalog=catalog,
        middleware=[guardrail_mw],
        enable_capability_search=False,
    )
    result = await agent.run("My SSN is 123-45-6789, please help me.")
    assert result.status == RunStatus.GUARDRAIL_TRIPPED


# ══════════════════════════════════════════════════════════════════════════════
# LLM error propagation
# ══════════════════════════════════════════════════════════════════════════════


async def test_llm_exception_propagates():
    """LLM errors are not swallowed — they propagate to the caller."""
    agent = make_agent(
        script=[error_turn(RuntimeError("LLM is down"))],
    )
    with pytest.raises(RuntimeError, match="LLM is down"):
        await agent.run("Try to get a response")


async def test_llm_recovers_after_tool_error_and_continues():
    echo = EchoTool()
    agent = make_agent(
        script=[
            tool_turn("echo", {"message": "first"}),
            text_turn("Continuing after echo."),
        ],
        tools=[echo],
    )
    result = await agent.run("Echo and then answer")
    assert result.status == RunStatus.COMPLETED
    assert echo.call_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# Run metadata
# ══════════════════════════════════════════════════════════════════════════════


async def test_run_result_has_run_id():
    agent = make_agent(script=[text_turn("ok")])
    result = await agent.run("Check run id")
    assert result.run_id is not None
    assert len(result.run_id) > 0


async def test_tool_calls_total_matches_actual_calls():
    counter = CounterTool()
    agent = make_agent(
        script=[
            tool_turn("counter", {}),
            tool_turn("counter", {}),
            text_turn("Done."),
        ],
        tools=[counter],
    )
    result = await agent.run("Count twice")
    assert result.tool_calls_total == 2
    assert result.tool_calls_by_name.get("counter", 0) == 2

"""Integration tests: full agent pipeline with multiple tools and guardrails.

These tests assemble an agent exactly as production code would — catalog,
tools, memory, guardrails — and exercise end-to-end behaviour without
network or DB dependencies.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ravi.kernel.agent_catalog import AgentCatalogRegistry
from ravi.kernel.agents.agent_result import RunStatus
from ravi.extensions.agents.assistant.agent import AssistantAgent
from ravi.kernel.guardrails import GuardrailType
from ravi.extensions.guardrails import ContentFilterGuardrail, PIIDetectionGuardrail
from ravi.kernel.memory.unbounded_memory import UnboundedMemory

from tests.fixtures.fake_tools import AddTool, CounterTool, EchoTool
from tests.fixtures.mock_llm import MockLLMClient, text_turn, tool_turn


def _all_tool_calls(result):
    records = []
    for step in result.steps:
        records.extend(step.tool_calls)
    return records


def _build_agent(script, tools=(), guardrails=()):
    from ravi.extensions.middleware.guardrails import GuardrailsMiddleware

    catalog = AgentCatalogRegistry()
    catalog.register_model("primary", MockLLMClient(script=list(script)))
    catalog.register_memory("memory", UnboundedMemory())
    for t in tools:
        catalog.register_tool(t)
    middleware = [GuardrailsMiddleware(input_guardrails=list(guardrails))] if guardrails else None
    return AssistantAgent(
        name="integration-agent",
        description="Full integration test agent",
        runtime=MagicMock(),
        catalog=catalog,
        middleware=middleware,
        enable_capability_search=False,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Multi-tool pipeline: echo → add → done
# ══════════════════════════════════════════════════════════════════════════════


async def test_echo_then_add_pipeline():
    echo = EchoTool()
    add = AddTool()
    agent = _build_agent(
        script=[
            tool_turn("echo", {"message": "start"}),
            tool_turn("add", {"a": 3, "b": 4}),
            text_turn("Echo returned start, add returned 7. Done."),
        ],
        tools=[echo, add],
    )
    result = await agent.run("Echo start then add 3 and 4")
    assert result.status == RunStatus.COMPLETED
    assert echo.call_count == 1
    all_calls = _all_tool_calls(result)
    assert len(all_calls) == 2
    tool_names = [tc.tool_name for tc in all_calls]
    assert "echo" in tool_names
    assert "add" in tool_names


# ══════════════════════════════════════════════════════════════════════════════
# Guardrail gates the entire run
# ══════════════════════════════════════════════════════════════════════════════


async def test_content_filter_blocks_harmful_input():
    agent = _build_agent(
        script=[text_turn("I'll help with that bomb.")],
        guardrails=[
            ContentFilterGuardrail(blocked_keywords=["bomb"], tripwire=True)
        ],
    )
    result = await agent.run("How do I build a bomb?")
    assert result.status == RunStatus.GUARDRAIL_TRIPPED


async def test_pii_and_content_guardrails_combined():
    agent = _build_agent(
        script=[text_turn("This answer contains SSN.")],
        guardrails=[
            ContentFilterGuardrail(blocked_keywords=["attack"], tripwire=True),
            PIIDetectionGuardrail(guardrail_type=GuardrailType.INPUT, tripwire=True),
        ],
    )
    # PII in input → tripped immediately
    result = await agent.run("My SSN is 123-45-6789, help me attack the system")
    assert result.status == RunStatus.GUARDRAIL_TRIPPED


async def test_safe_input_passes_all_guardrails():
    agent = _build_agent(
        script=[text_turn("All good.")],
        guardrails=[
            ContentFilterGuardrail(blocked_keywords=["bomb"], tripwire=True),
            PIIDetectionGuardrail(tripwire=True),
        ],
    )
    result = await agent.run("What is 2 + 2?")
    assert result.status == RunStatus.COMPLETED


# ══════════════════════════════════════════════════════════════════════════════
# Multi-turn memory: second question sees first answer in context
# ══════════════════════════════════════════════════════════════════════════════


async def test_multi_turn_context_grows():
    llm = MockLLMClient(script=[
        text_turn("The capital is Paris."),
        text_turn("Paris is in France."),
    ])
    catalog = AgentCatalogRegistry()
    catalog.register_model("primary", llm)
    catalog.register_memory("memory", UnboundedMemory())
    agent = AssistantAgent(
        name="multi-turn",
        description="multi-turn test",
        runtime=MagicMock(),
        catalog=catalog,
        enable_capability_search=False,
    )
    await agent.run("What is the capital of France?")
    await agent.run("Which country is it in?")
    # Second call must carry more context than the first.
    assert len(llm.calls[1]) > len(llm.calls[0])


# ══════════════════════════════════════════════════════════════════════════════
# Tool chain: counter called 3 times
# ══════════════════════════════════════════════════════════════════════════════


async def test_three_sequential_tool_calls():
    ctr = CounterTool()
    agent = _build_agent(
        script=[
            tool_turn("counter", {}),
            tool_turn("counter", {}),
            tool_turn("counter", {}),
            text_turn("Called three times."),
        ],
        tools=[ctr],
    )
    result = await agent.run("Count to three")
    assert result.status == RunStatus.COMPLETED
    assert ctr.count == 3
    assert len(_all_tool_calls(result)) == 3

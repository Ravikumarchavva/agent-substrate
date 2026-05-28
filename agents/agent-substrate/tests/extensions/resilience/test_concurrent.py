"""Tests for concurrent agent runs — isolation between parallel runs."""

from __future__ import annotations

import asyncio

from ravi.kernel.agents.agent_result import RunStatus
from ravi.kernel.agent_catalog import AgentCatalogRegistry
from ravi.kernel.memory.unbounded_memory import UnboundedMemory
from ravi.extensions.agents.react.agent import ReActAgent

from tests.fixtures.mock_llm import MockLLMClient, text_turn, tool_turn
from tests.fixtures.fake_tools import CounterTool
from tests.extensions.agents.react.conftest import make_agent


# ══════════════════════════════════════════════════════════════════════════════
# N agents in parallel share nothing
# ══════════════════════════════════════════════════════════════════════════════


async def test_parallel_agents_do_not_share_state():
    n = 5
    agents = [make_agent(script=[text_turn(f"response {i}")]) for i in range(n)]
    results = await asyncio.gather(*[a.run(f"query {i}") for i, a in enumerate(agents)])
    assert all(r.status == RunStatus.COMPLETED for r in results)
    # Each agent should have seen exactly 1 LLM call.
    for i, agent in enumerate(agents):
        llm: MockLLMClient = agent.catalog.primary_model()  # type: ignore[assignment]
        assert len(llm.calls) == 1, f"Agent {i} had unexpected call count"


# ══════════════════════════════════════════════════════════════════════════════
# Single agent: sequential runs stay isolated
# ══════════════════════════════════════════════════════════════════════════════


async def test_sequential_runs_on_same_agent_accumulate_memory():
    llm = MockLLMClient(script=[
        text_turn("First answer."),
        text_turn("Second answer."),
    ])
    catalog = AgentCatalogRegistry()
    catalog.register_model("primary", llm)
    catalog.register_memory("memory", UnboundedMemory())

    agent = ReActAgent(
        name="seq-agent",
        description="sequential test",
        catalog=catalog,
        enable_capability_search=False,
    )
    r1 = await agent.run("Turn one")
    r2 = await agent.run("Turn two")
    assert r1.status == RunStatus.COMPLETED
    assert r2.status == RunStatus.COMPLETED
    # Second run should have seen the history from the first turn.
    assert len(llm.calls[1]) > len(llm.calls[0])


# ══════════════════════════════════════════════════════════════════════════════
# Shared tool: concurrent calls don't corrupt state
# ══════════════════════════════════════════════════════════════════════════════


async def test_concurrent_tool_calls_do_not_corrupt_counter():
    # Each agent gets its OWN CounterTool — they must not share state.
    agents = []
    counters = []
    for _ in range(4):
        ctr = CounterTool()
        counters.append(ctr)
        agents.append(
            make_agent(
                script=[tool_turn("counter", {}), text_turn("done")],
                tools=[ctr],
            )
        )
    await asyncio.gather(*[a.run("count once") for a in agents])
    # Each counter should have been called exactly once.
    for ctr in counters:
        assert ctr.count == 1

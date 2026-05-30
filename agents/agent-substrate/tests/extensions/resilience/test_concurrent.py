"""Tests for concurrent agent runs — isolation between parallel runs."""

from __future__ import annotations

import asyncio

from ravi.fabric.agents_base.agent_result import RunStatus
from ravi.fabric.catalog import AgentCatalogRegistry
from ravi.fabric.memory.in_memory import InMemoryHistoryProvider
from ravi.fabric.runtime.local import LocalRuntime
from ravi.reasoning.agents.assistant.agent import AssistantAgent

from tests.fixtures.mock_llm import MockLLMClient, text_turn, tool_turn
from tests.fixtures.fake_tools import CounterTool
from tests.extensions.agents.assistant.conftest import make_agent


# ══════════════════════════════════════════════════════════════════════════════
# N agents in parallel share nothing
# ══════════════════════════════════════════════════════════════════════════════


async def test_parallel_agents_do_not_share_state():
    n = 5
    async with LocalRuntime() as rt:
        agents = [
            await make_agent(script=[text_turn(f"response {i}")], runtime=rt)
            for i in range(n)
        ]
        results = await asyncio.gather(*[a.run(f"query {i}") for i, a in enumerate(agents)])
    assert all(r.status == RunStatus.COMPLETED for r in results)
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
    catalog.register_memory("memory", InMemoryHistoryProvider())

    async with LocalRuntime() as rt:
        agent = AssistantAgent(
            "seq-agent",
            rt,
            catalog=catalog,
            enable_capability_search=False,
        )
        await agent.start()
        r1 = await agent.run("Turn one")
        r2 = await agent.run("Turn two")
    assert r1.status == RunStatus.COMPLETED
    assert r2.status == RunStatus.COMPLETED
    assert len(llm.calls[1]) > len(llm.calls[0])


# ══════════════════════════════════════════════════════════════════════════════
# Shared tool: concurrent calls don't corrupt state
# ══════════════════════════════════════════════════════════════════════════════


async def test_concurrent_tool_calls_do_not_corrupt_counter():
    async with LocalRuntime() as rt:
        agents = []
        counters = []
        for _ in range(4):
            ctr = CounterTool()
            counters.append(ctr)
            agents.append(
                await make_agent(
                    script=[tool_turn("counter", {}), text_turn("done")],
                    tools=[ctr],
                    runtime=rt,
                )
            )
        await asyncio.gather(*[a.run("count once") for a in agents])
    for ctr in counters:
        assert ctr.count == 1

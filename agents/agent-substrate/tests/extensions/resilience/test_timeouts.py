"""Tests for timeout enforcement in the agent run loop."""

from __future__ import annotations

import asyncio
import pytest

from ravi.kernel.agents.agent_result import RunStatus
from ravi.kernel.runtime._local import LocalRuntime

from tests.fixtures.mock_llm import tool_turn, text_turn
from tests.fixtures.fake_tools import SlowTool
from tests.extensions.agents.assistant.conftest import make_agent


async def test_run_timeout_raises():
    slow = SlowTool(seconds=30.0)
    async with LocalRuntime() as rt:
        agent = await make_agent(
            script=[tool_turn("slow", {}), text_turn("never reached")],
            tools=[slow],
            run_timeout=0.2,
            runtime=rt,
        )
        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            await agent.run("Take forever please")


async def test_tool_timeout_surfaces_error_to_llm():
    slow = SlowTool(seconds=30.0)
    async with LocalRuntime() as rt:
        agent = await make_agent(
            script=[tool_turn("slow", {}), text_turn("The tool timed out.")],
            tools=[slow],
            tool_timeout=0.1,
            runtime=rt,
        )
        result = await agent.run("Call the slow tool")
    assert result.status == RunStatus.COMPLETED
    all_calls = [tc for step in result.steps for tc in step.tool_calls]
    errored = [tc for tc in all_calls if tc.is_error]
    assert len(errored) >= 1


async def test_fast_tool_completes_within_timeout():
    from tests.fixtures.fake_tools import EchoTool
    echo = EchoTool()
    async with LocalRuntime() as rt:
        agent = await make_agent(
            script=[tool_turn("echo", {"message": "quick"}), text_turn("Done.")],
            tools=[echo],
            tool_timeout=5.0,
            runtime=rt,
        )
        result = await agent.run("Call echo quickly")
    assert result.status == RunStatus.COMPLETED

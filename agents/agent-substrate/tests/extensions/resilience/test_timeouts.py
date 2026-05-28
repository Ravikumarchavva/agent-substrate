"""Tests for timeout enforcement in the agent run loop.

run_timeout   — overall wall-clock cap on the entire agent.run() call.
tool_timeout  — per-tool cap passed to _execute_tool.
"""

from __future__ import annotations

import asyncio
import pytest

from ravi.kernel.agents.agent_result import RunStatus

from tests.fixtures.mock_llm import tool_turn, text_turn
from tests.fixtures.fake_tools import SlowTool
from tests.extensions.agents.react.conftest import make_agent


# ══════════════════════════════════════════════════════════════════════════════
# run_timeout: entire run is capped
# ══════════════════════════════════════════════════════════════════════════════


async def test_run_timeout_raises():
    """run_timeout uses asyncio.wait_for which raises TimeoutError on expiry."""
    slow = SlowTool(seconds=30.0)
    agent = make_agent(
        script=[
            tool_turn("slow", {}),
            text_turn("never reached"),
        ],
        tools=[slow],
        run_timeout=0.2,
    )
    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await agent.run("Take forever please")


# ══════════════════════════════════════════════════════════════════════════════
# tool_timeout: individual tool call is capped
# ══════════════════════════════════════════════════════════════════════════════


async def test_tool_timeout_surfaces_error_to_llm():
    slow = SlowTool(seconds=30.0)
    agent = make_agent(
        script=[
            tool_turn("slow", {}),
            text_turn("The tool timed out."),
        ],
        tools=[slow],
        tool_timeout=0.1,
    )
    result = await agent.run("Call the slow tool")
    # Tool timeout should be caught and fed back as an error observation;
    # the agent continues and ultimately completes.
    assert result.status == RunStatus.COMPLETED
    all_calls = [tc for step in result.steps for tc in step.tool_calls]
    errored = [tc for tc in all_calls if tc.is_error]
    assert len(errored) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# Fast tool: no timeout triggered
# ══════════════════════════════════════════════════════════════════════════════


async def test_fast_tool_completes_within_timeout():
    from tests.fixtures.fake_tools import EchoTool
    echo = EchoTool()
    agent = make_agent(
        script=[
            tool_turn("echo", {"message": "quick"}),
            text_turn("Done."),
        ],
        tools=[echo],
        tool_timeout=5.0,
    )
    result = await agent.run("Call echo quickly")
    assert result.status == RunStatus.COMPLETED
    all_calls = [tc for step in result.steps for tc in step.tool_calls]
    assert not any(tc.is_error for tc in all_calls)

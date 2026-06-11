"""Kernel→wire mapper tests."""

from __future__ import annotations

from ravi.kernel.content import TextBlock, ToolUseBlock
from ravi.kernel.identity import AgentId
from ravi.kernel.stream import (
    AgentProgress,
    AgentStep,
    CompletionEvent,
    ReasoningDelta,
    StreamDone,
    TextDelta,
)
from ravi.kernel.content import UIResourceBlock
from ravi.serving.protocol import (
    HandoffEvent,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    TurnCompletedEvent,
    UIResourceEvent,
)
from ravi.serving.stream.mapper import map_kernel_event

_AID = AgentId(type="assistant", key="researcher")


def test_text_and_reasoning_deltas() -> None:
    assert map_kernel_event(TextDelta(text="hi")) == TextDeltaEvent(text="hi")
    assert map_kernel_event(ReasoningDelta(text="hmm")) == ReasoningDeltaEvent(
        text="hmm"
    )


def test_tool_call_progress_maps_with_agent_and_depth() -> None:
    ev = AgentProgress(
        agent_id=_AID,
        step=AgentStep.TOOL_CALL,
        content="web_search",
        run_id="r",
        depth=1,
        metadata={"call_id": "c1"},
    )
    wire = map_kernel_event(ev)
    assert isinstance(wire, ToolCallEvent)
    assert wire.tool_name == "web_search"
    assert wire.call_id == "c1"
    assert wire.agent == "researcher"
    assert wire.depth == 1


def test_tool_result_ok_and_error() -> None:
    ok = map_kernel_event(
        AgentProgress(
            agent_id=_AID,
            step=AgentStep.TOOL_RESULT,
            content="web_search: ok",
            run_id="r",
            depth=1,
            metadata={"call_id": "c1"},
        )
    )
    assert (
        isinstance(ok, ToolResultEvent)
        and ok.ok is True
        and ok.tool_name == "web_search"
    )

    err = map_kernel_event(
        AgentProgress(
            agent_id=_AID,
            step=AgentStep.TOOL_RESULT,
            content="web_search: error",
            run_id="r",
            depth=1,
            metadata={"call_id": "c1"},
        )
    )
    assert isinstance(err, ToolResultEvent) and err.ok is False and err.error


def test_tool_result_with_ui_fans_out_to_ui_resource() -> None:
    block = UIResourceBlock(
        uri="ui://kanban_board",
        structured_content={"task_list": {"tasks": []}},
        render="panel",
    )
    out = map_kernel_event(
        AgentProgress(
            agent_id=_AID,
            step=AgentStep.TOOL_RESULT,
            content="task_board: ok",
            run_id="r",
            depth=0,
            metadata={"call_id": "c9", "ui": block.model_dump_json()},
        )
    )
    assert isinstance(out, list) and len(out) == 2
    result, ui = out
    assert isinstance(result, ToolResultEvent) and result.ok is True
    assert isinstance(ui, UIResourceEvent)
    assert ui.uri == "ui://kanban_board"
    assert ui.call_id == "c9"
    assert ui.render == "panel"
    assert ui.structured_content == {"task_list": {"tasks": []}}


def test_handoff_tool_call_becomes_handoff_event() -> None:
    ev = AgentProgress(
        agent_id=AgentId(type="assistant", key="coordinator"),
        step=AgentStep.TOOL_CALL,
        content="handoff_researcher",
        run_id="r",
        depth=0,
    )
    wire = map_kernel_event(ev)
    assert isinstance(wire, HandoffEvent)
    assert wire.source_agent == "coordinator"
    assert wire.target_agent == "researcher"


def test_handoff_tool_result_is_dropped() -> None:
    ev = AgentProgress(
        agent_id=AgentId(type="assistant", key="coordinator"),
        step=AgentStep.TOOL_RESULT,
        content="handoff_researcher: ok",
        run_id="r",
    )
    assert map_kernel_event(ev) is None


def test_completion_extracts_text_and_tool_calls() -> None:
    ev = CompletionEvent(
        content=[
            TextBlock(text="The answer is "),
            TextBlock(text="42"),
            ToolUseBlock(
                call_id="t1", tool_name="calculator", arguments={"expr": "6*7"}
            ),
        ]
    )
    wire = map_kernel_event(ev)
    assert isinstance(wire, TurnCompletedEvent)
    assert wire.text == "The answer is 42"
    assert len(wire.tool_calls) == 1
    assert wire.tool_calls[0].name == "calculator"
    assert wire.tool_calls[0].args == {"expr": "6*7"}


def test_thinking_and_streamdone_are_dropped() -> None:
    thinking = AgentProgress(
        agent_id=_AID, step=AgentStep.THINKING, content="step 1", run_id="r"
    )
    assert map_kernel_event(thinking) is None
    assert map_kernel_event(StreamDone(reason="success")) is None

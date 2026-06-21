"""Log-entry → wire-event deserialization tests.

The kernel log ``kind``/``payload`` and the wire protocol ``type``/fields are one
vocabulary, so ``wire_from_log`` is pure validation — these tests pin that the
two stay aligned (a drift in field names would surface here).
"""

from __future__ import annotations

from agent_substrate.serving.monolith.sse.bridge import bridge_event_to_wire
from agent_substrate.serving.protocol import (
    ApprovalRequestedEvent,
    InputRequestedEvent,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    wire_from_log,
)


# ---------------------------------------------------------------------------
# wire_from_log — streaming kinds
# ---------------------------------------------------------------------------


def test_text_delta() -> None:
    assert wire_from_log("text.delta", {"text": "hi"}) == TextDeltaEvent(text="hi")


def test_reasoning_delta() -> None:
    assert wire_from_log("reasoning.delta", {"text": "hmm"}) == ReasoningDeltaEvent(
        text="hmm"
    )


def test_tool_call() -> None:
    ev = wire_from_log(
        "tool.call",
        {"call_id": "c1", "tool_name": "search", "args": {"q": "x"}},
    )
    assert ev == ToolCallEvent(call_id="c1", tool_name="search", args={"q": "x"})


def test_tool_result_ok() -> None:
    ev = wire_from_log(
        "tool.result",
        {"call_id": "c1", "tool_name": "search", "ok": True, "output": "done"},
    )
    assert isinstance(ev, ToolResultEvent)
    assert ev.ok is True
    assert ev.output == "done"


def test_tool_result_error() -> None:
    ev = wire_from_log(
        "tool.result",
        {"call_id": "c1", "tool_name": "search", "ok": False, "error": "boom"},
    )
    assert isinstance(ev, ToolResultEvent)
    assert ev.ok is False
    assert ev.error == "boom"


def test_non_streaming_kinds_return_none() -> None:
    assert wire_from_log("run.completed", {}) is None
    assert wire_from_log("llm.call", {"model": "gpt"}) is None
    assert wire_from_log("ask.replied", {}) is None


# ---------------------------------------------------------------------------
# bridge_event_to_wire — out-of-band HITL dicts
# ---------------------------------------------------------------------------


def test_bridge_approval_request() -> None:
    ev = bridge_event_to_wire(
        {
            "type": "tool_approval_request",
            "request_id": "r1",
            "tool_name": "delete",
            "arguments": {"path": "/tmp"},
        }
    )
    assert ev == ApprovalRequestedEvent(
        request_id="r1", tool_name="delete", args={"path": "/tmp"}
    )


def test_bridge_input_request() -> None:
    ev = bridge_event_to_wire(
        {"type": "human_input_request", "request_id": "r2", "question": "ok?"}
    )
    assert isinstance(ev, InputRequestedEvent)
    assert ev.request_id == "r2"
    assert ev.question == "ok?"


def test_bridge_unknown_returns_none() -> None:
    assert bridge_event_to_wire({"type": "something_else"}) is None

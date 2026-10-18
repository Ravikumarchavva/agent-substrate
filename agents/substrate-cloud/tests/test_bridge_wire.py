"""``bridge_event_to_wire`` — out-of-band HITL dicts to wire events (the monolith's SSE bridge)."""

from __future__ import annotations

from substrate.server.protocol import ApprovalRequestedEvent, InputRequestedEvent
from substrate_cloud.monolith.sse.bridge import bridge_event_to_wire


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

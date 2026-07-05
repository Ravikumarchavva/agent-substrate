"""Protocol contract tests — every wire event round-trips and the schema exports."""

from __future__ import annotations

import json

from pydantic import TypeAdapter

from substrate.serving.protocol import (
    PROTOCOL_VERSION,
    WireEvent,
    HelloEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    HandoffEvent,
    TurnCompletedEvent,
    ToolCallSummary,
    ApprovalRequestedEvent,
    RunFailedEvent,
    ErrorEvent,
)
from substrate.serving.protocol.export import build_schema

_ADAPTER = TypeAdapter(WireEvent)


def _roundtrip(event: object) -> object:
    """Serialize to JSON and validate back through the discriminated union."""
    payload = event.model_dump(mode="json")  # type: ignore[attr-defined]
    raw = json.loads(json.dumps(payload))  # ensure it's JSON-safe
    return _ADAPTER.validate_python(raw)


def test_hello_carries_current_version() -> None:
    assert HelloEvent().version == PROTOCOL_VERSION


def test_every_event_roundtrips_via_discriminator() -> None:
    events = [
        HelloEvent(),
        TextDeltaEvent(text="hi"),
        ToolCallEvent(
            call_id="c1",
            tool_name="web_search",
            args={"q": "x"},
            agent="researcher",
            depth=1,
        ),
        ToolResultEvent(
            call_id="c1",
            tool_name="web_search",
            ok=True,
            output="done",
            agent="researcher",
            depth=1,
        ),
        HandoffEvent(
            source_agent="coordinator",
            target_agent="researcher",
            reason="needs facts",
            depth=0,
        ),
        TurnCompletedEvent(
            text="answer",
            tool_calls=[ToolCallSummary(id="t1", name="calc")],
            finish_reason="stop",
        ),
        ApprovalRequestedEvent(
            request_id="r1", tool_name="send_email", args={"to": "a@b.c"}
        ),
        RunFailedEvent(error="boom", code="crash"),
        ErrorEvent(message="bad"),
    ]
    for ev in events:
        back = _roundtrip(ev)
        assert type(back) is type(ev), f"{type(ev).__name__} did not round-trip"
        assert back.type == ev.type  # type: ignore[attr-defined]


def test_tool_error_result_marks_not_ok() -> None:
    ev = ToolResultEvent(call_id="c", tool_name="t", ok=False, error="timeout")
    back = _roundtrip(ev)
    assert isinstance(back, ToolResultEvent)
    assert back.ok is False
    assert back.error == "timeout"


def test_schema_export_is_valid_and_versioned() -> None:
    schema = build_schema()
    assert schema["title"] == "SubstrateProtocol"
    assert schema["x-protocol-version"] == PROTOCOL_VERSION
    # All event models must appear in $defs so codegen emits every interface.
    defs = schema["$defs"]
    for name in ("HelloEvent", "ToolCallEvent", "TurnCompletedEvent", "ChatRequest"):
        assert name in defs, f"{name} missing from exported schema $defs"

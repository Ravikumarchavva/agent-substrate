"""Log-entry → wire-event deserialization tests.

The kernel log ``kind``/``payload`` and the wire protocol ``type``/fields are one
vocabulary, so ``wire_from_log`` is pure validation — these tests pin that the
two stay aligned (a drift in field names would surface here).
"""

from __future__ import annotations

from substrate.server.protocol import (
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    wire_from_log,
)
from substrate.server.protocol.events import MessageFlaggedEvent


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


def test_user_message_flagged_survives_reload_via_streaming_kinds() -> None:
    """The reload-visibility half of persist-but-exclude: a flagged
    message's marker must be a real streaming kind (not just live-SSE-only,
    like run.failed today), or the badge would vanish on page refresh."""
    ev = wire_from_log(
        "user.message.flagged",
        {
            "seq": 7,
            "detector": "prompt_guard",
            "severity": "high",
            "scores": {"malicious": 0.97},
            "modality": "text",
        },
    )
    assert ev == MessageFlaggedEvent(
        seq=7,
        detector="prompt_guard",
        severity="high",
        scores={"malicious": 0.97},
        modality="text",
    )

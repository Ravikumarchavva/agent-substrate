from __future__ import annotations

from substrate.kernel.messaging.stream import (
    TextDelta,
    ReasoningDelta,
    CompletionEvent,
    StreamDone,
)
from substrate.kernel.core.content import TextBlock


def test_text_delta():
    event = TextDelta(text="hello delta")
    assert event.text == "hello delta"


def test_reasoning_delta():
    event = ReasoningDelta(text="thinking delta")
    assert event.text == "thinking delta"


def test_completion_event():
    blocks = [TextBlock(text="final")]
    event = CompletionEvent(content=blocks, metadata={"usage": "10"})
    assert event.content == blocks
    assert event.metadata == {"usage": "10"}


def test_stream_done():
    event = StreamDone(reason="success")
    assert event.reason == "success"

"""Tests for EventBus typed event system and WebHITLBridge sentinel contracts."""

from __future__ import annotations

import asyncio
import json

import pytest

from raavan.server.sse.events import (
    BUS_CLOSED,
    CompletionEvent,
    ErrorEvent,
    EventBus,
    RawDictEvent,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    _BUS_DONE,
)
from raavan.server.sse.bridge import BRIDGE_DONE, WebHITLBridge, _DONE


# ---------------------------------------------------------------------------
# EventBus -- emit / consume
# ---------------------------------------------------------------------------


async def test_emit_and_consume_in_order():
    bus = EventBus()
    await bus.emit(TextDeltaEvent(content="a"))
    await bus.emit(TextDeltaEvent(content="b"))
    bus.close()
    collected = []
    async for event in bus:
        collected.append(event.content)
    assert collected == ["a", "b"]


async def test_close_is_idempotent():
    bus = EventBus()
    await bus.emit(TextDeltaEvent(content="x"))
    bus.close()
    bus.close()  # second close must not raise or push a duplicate sentinel
    items = []
    async for event in bus:
        items.append(event)
    assert len(items) == 1


async def test_emit_after_close_is_noop():
    bus = EventBus()
    bus.close()
    await bus.emit(TextDeltaEvent(content="dropped"))
    items = []
    async for event in bus:
        items.append(event)
    assert items == []


async def test_emit_dict_produces_raw_dict_event():
    bus = EventBus()
    await bus.emit_dict({"type": "task_updated", "value": 42})
    bus.close()
    items = []
    async for event in bus:
        items.append(event)
    assert len(items) == 1
    evt = items[0]
    assert isinstance(evt, RawDictEvent)
    assert evt.data["type"] == "task_updated"
    assert evt.data["value"] == 42


# ---------------------------------------------------------------------------
# EventBus.poll()
# ---------------------------------------------------------------------------


async def test_poll_returns_bus_closed_sentinel():
    bus = EventBus()
    bus.close()
    assert await bus.poll(1.0) is BUS_CLOSED


async def test_poll_raises_timeout_when_empty():
    bus = EventBus()
    with pytest.raises(asyncio.TimeoutError):
        await bus.poll(0.05)


async def test_poll_returns_event():
    bus = EventBus()
    await bus.emit(TextDeltaEvent(content="hello"))
    event = await bus.poll(1.0)
    assert isinstance(event, TextDeltaEvent)
    assert event.content == "hello"


async def test_poll_sequence_terminates_at_bus_closed():
    """Consumer loop using poll() must stop when BUS_CLOSED is returned."""
    bus = EventBus()
    await bus.emit(TextDeltaEvent(content="one"))
    await bus.emit(ReasoningDeltaEvent(content="two"))
    bus.close()

    results = []
    while True:
        try:
            item = await bus.poll(1.0)
        except asyncio.TimeoutError:
            break
        if item is BUS_CLOSED:
            break
        results.append(item)
    assert len(results) == 2
    assert results[0].content == "one"
    assert results[1].content == "two"


# ---------------------------------------------------------------------------
# Sentinel identity
# ---------------------------------------------------------------------------


def test_bus_closed_is_internal_sentinel():
    assert BUS_CLOSED is _BUS_DONE


def test_bridge_done_is_internal_sentinel():
    assert BRIDGE_DONE is _DONE


def test_bus_closed_and_bridge_done_are_distinct():
    """The two sentinels must not be the same object."""
    assert BUS_CLOSED is not BRIDGE_DONE


# ---------------------------------------------------------------------------
# to_sse_line format
# ---------------------------------------------------------------------------


def test_to_sse_line_format():
    bus = EventBus()
    line = bus.to_sse_line(TextDeltaEvent(content="hi"))
    assert line.startswith("data: ")
    assert line.endswith("\n\n")
    data = json.loads(line[len("data: ") : -2])
    assert data["type"] == "text_delta"
    assert data["content"] == "hi"
    assert data["partial"] is True


# ---------------------------------------------------------------------------
# to_dict round-trips
# ---------------------------------------------------------------------------


def test_text_delta_to_dict():
    d = TextDeltaEvent(content="foo", partial=True).to_dict()
    assert d == {"type": "text_delta", "content": "foo", "partial": True}


def test_reasoning_delta_to_dict():
    d = ReasoningDeltaEvent(content="think", partial=False).to_dict()
    assert d == {"type": "reasoning_delta", "content": "think", "partial": False}


def test_completion_to_dict():
    d = CompletionEvent(message="done").to_dict()
    assert d == {"type": "completion", "message": "done"}


def test_error_to_dict_with_code():
    d = ErrorEvent(message="oops", code="E001").to_dict()
    assert d == {"type": "error", "message": "oops", "code": "E001"}


def test_error_to_dict_no_code():
    d = ErrorEvent(message="fail").to_dict()
    assert d == {"type": "error", "message": "fail"}
    assert "code" not in d


def test_raw_dict_event_to_dict_passthrough():
    payload = {"type": "task_updated", "x": 1}
    d = RawDictEvent(type="task_updated", data=payload).to_dict()
    assert d is payload


# ---------------------------------------------------------------------------
# WebHITLBridge.cancel_all_pending
# ---------------------------------------------------------------------------


async def test_cancel_all_pending_resolves_futures_with_session_disconnected():
    bridge = WebHITLBridge()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    bridge._pending["req-1"] = fut
    bridge._pending_payloads["req-1"] = {"type": "tool_approval_request"}

    count = bridge.cancel_all_pending("session_disconnected")
    assert count == 1
    assert fut.result()["session_disconnected"] is True
    assert fut.result()["reason"] == "session_disconnected"


async def test_cancel_all_pending_clears_all_state():
    bridge = WebHITLBridge()
    loop = asyncio.get_running_loop()
    for i in range(3):
        fut: asyncio.Future = loop.create_future()
        bridge._pending[f"req-{i}"] = fut
        bridge._pending_payloads[f"req-{i}"] = {}

    bridge.cancel_all_pending()
    assert len(bridge._pending) == 0
    assert len(bridge._pending_payloads) == 0


async def test_cancel_all_pending_noop_when_empty():
    bridge = WebHITLBridge()
    assert bridge.cancel_all_pending("session_disconnected") == 0


async def test_cancel_all_pending_skips_already_resolved_futures():
    bridge = WebHITLBridge()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    fut.set_result({"approved": True})  # already resolved
    bridge._pending["req-1"] = fut

    assert bridge.cancel_all_pending() == 0


async def test_cancel_all_pending_custom_reason():
    bridge = WebHITLBridge()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    bridge._pending["req-1"] = fut

    bridge.cancel_all_pending("server_restart")
    result = fut.result()
    assert result["session_disconnected"] is True
    assert result["reason"] == "server_restart"

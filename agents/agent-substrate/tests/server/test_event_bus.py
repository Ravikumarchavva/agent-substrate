"""Tests for WebHITLBridge sentinel contracts."""

from __future__ import annotations

import asyncio

from agent_substrate.serving.monolith.sse.bridge import BRIDGE_DONE, WebHITLBridge, _DONE


def test_bridge_done_is_internal_sentinel():
    assert BRIDGE_DONE is _DONE


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
    fut.set_result({"approved": True})
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

"""Tests for SlidingWindowContext — the default context strategy."""

from __future__ import annotations

import pytest

from ravi.extensions.context.sliding_window import SlidingWindowContext
from ravi.kernel.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    UserMessage,
)


def _sys(text: str = "You are an agent.") -> SystemMessage:
    return SystemMessage(content=text)


def _user(text: str) -> UserMessage:
    return UserMessage(content=[text])


def _asst(text: str) -> AssistantMessage:
    return AssistantMessage(content=[text])


def _build(ctx, msgs):
    """Helper: call build() with the required keyword args."""
    return ctx.build(
        session_id="test",
        current_input="",
        raw_messages=msgs,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Basic window behaviour
# ══════════════════════════════════════════════════════════════════════════════


async def test_fewer_messages_than_window_returned_unchanged():
    ctx = SlidingWindowContext(max_messages=10)
    msgs = [_sys(), _user("hi"), _asst("hello")]
    result = await _build(ctx, msgs)
    assert len(result) == 3


async def test_window_keeps_last_n_plus_system():
    ctx = SlidingWindowContext(max_messages=3)
    msgs = [_sys()] + [_user(f"msg {i}") for i in range(6)]
    result = await _build(ctx, msgs)
    # system + last 3
    assert result[0].role == "system"
    assert len(result) == 4
    contents = [m.content for m in result[1:]]
    assert ["msg 5"] in contents
    assert ["msg 2"] not in contents


async def test_system_message_always_first():
    ctx = SlidingWindowContext(max_messages=2)
    msgs = [_sys("sys")] + [_user(f"u{i}") for i in range(5)]
    result = await _build(ctx, msgs)
    assert result[0].role == "system"


async def test_no_system_message_window_still_works():
    ctx = SlidingWindowContext(max_messages=3)
    msgs = [_user(f"m{i}") for i in range(5)]
    result = await _build(ctx, msgs)
    assert len(result) == 3


# ══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════════════════════


async def test_empty_input_returns_empty():
    ctx = SlidingWindowContext(max_messages=5)
    result = await _build(ctx, [])
    assert result == []


async def test_only_system_message():
    ctx = SlidingWindowContext(max_messages=5)
    result = await _build(ctx, [_sys()])
    assert len(result) == 1
    assert result[0].role == "system"


async def test_window_size_one():
    ctx = SlidingWindowContext(max_messages=1)
    msgs = [_sys()] + [_user(f"msg{i}") for i in range(4)]
    result = await _build(ctx, msgs)
    # system + last 1
    assert len(result) == 2
    assert result[-1].content == ["msg3"]


async def test_max_messages_zero_raises():
    with pytest.raises(ValueError, match="max_messages must be"):
        SlidingWindowContext(max_messages=0)

"""build_memory_tool() — gating (short/long/both/neither) and the fix that
keys long-term ops by user_id, not session_id."""

from __future__ import annotations

from substrate.infrastructure.serving_factory import build_memory_tool


def test_returns_none_when_neither_backend_configured():
    assert build_memory_tool("session-1", "user-1", None, None) is None


def test_returns_a_tool_with_only_short_term_configured():
    """Previously this whole function gated on short_term alone, so a
    long-term-only deployment got no tool at all — that bug is what this
    guards against, from the other side."""
    tool = build_memory_tool("session-1", "user-1", object(), None)
    assert tool is not None


def test_returns_a_tool_with_only_long_term_configured():
    tool = build_memory_tool("session-1", "user-1", None, object())
    assert tool is not None


def test_returns_a_tool_with_both_configured():
    tool = build_memory_tool("session-1", "user-1", object(), object())
    assert tool is not None


def test_long_term_ops_are_keyed_by_user_id_not_session_id():
    """The whole point of this change: two different sessions for the same
    user must resolve to the identical agent_id for remember/recall/forget,
    so a fact saved in one thread is visible in another."""
    tool_a = build_memory_tool("session-1", "user-42", None, object())
    tool_b = build_memory_tool("session-2", "user-42", None, object())

    assert str(tool_a._agent_id) == str(tool_b._agent_id)
    assert "user-42" in str(tool_a._agent_id)
    assert "session-1" not in str(tool_a._agent_id)
    assert "session-2" not in str(tool_b._agent_id)

    # Short-term state stays session-scoped, unaffected by the agent_id fix.
    assert tool_a._session_id == "session-1"
    assert tool_b._session_id == "session-2"


def test_falls_back_to_session_scope_when_no_user_id():
    """No authenticated user (user_id=None) degrades long-term memory to
    per-session scope rather than erroring — matches pre-fix behavior for
    that case instead of breaking it."""
    tool = build_memory_tool("session-1", None, None, object())
    assert "session-1" in str(tool._agent_id)

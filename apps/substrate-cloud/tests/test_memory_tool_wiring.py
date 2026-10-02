"""build_memory_tool() — gating (short/long/both/neither) — and whose long-term memory the
tool reads and writes (the person the run is for, taken from the run's scope)."""

from __future__ import annotations

from types import SimpleNamespace

from substrate.types import RunScope
from substrate.stores import Store
from substrate_cloud.factory import build_memory_tool


def _ctx(user: str | None, thread: str, tenant: str | None = "acme") -> SimpleNamespace:
    return SimpleNamespace(scope=RunScope(tenant_id=tenant, user_id=user, thread_id=thread))


def test_returns_none_when_neither_backend_configured():
    assert build_memory_tool("session-1", None, None) is None


def test_returns_a_tool_with_only_short_term_configured():
    """Previously this whole function gated on short_term alone, so a
    long-term-only deployment got no tool at all — that bug is what this
    guards against, from the other side."""
    assert build_memory_tool("session-1", object(), None) is not None


def test_returns_a_tool_with_only_long_term_configured():
    assert build_memory_tool("session-1", None, object()) is not None


def test_returns_a_tool_with_both_configured():
    assert build_memory_tool("session-1", object(), object()) is not None


async def test_a_users_long_term_facts_follow_them_across_threads(tmp_path):
    """The point of keying by user, not session: a fact saved in one thread is visible in
    another thread the same user opens later — and never to anyone else."""
    store = Store.at(tmp_path).memory
    in_thread_1 = build_memory_tool("session-1", None, store)
    in_thread_2 = build_memory_tool("session-2", None, store)

    saved = await in_thread_1.execute(ctx=_ctx("user-42", "session-1"), action="remember", value="prefers French")
    assert not saved.is_error

    same_user = await in_thread_2.execute(ctx=_ctx("user-42", "session-2"), action="recall", query="French")
    other_user = await in_thread_2.execute(ctx=_ctx("user-43", "session-2"), action="recall", query="French")
    other_tenant = await in_thread_2.execute(ctx=_ctx("user-42", "session-2", tenant="evilcorp"), action="recall", query="French")

    assert "prefers French" in same_user.content[0].text
    assert "No relevant memories" in other_user.content[0].text
    assert "No relevant memories" in other_tenant.content[0].text


async def test_with_no_user_a_fact_is_kept_for_that_conversation_only(tmp_path):
    """No authenticated user degrades long-term memory to the conversation rather than erroring."""
    store = Store.at(tmp_path).memory
    tool = build_memory_tool("session-1", None, store)

    await tool.execute(ctx=_ctx(None, "session-1"), action="remember", value="anonymous note")

    same = await tool.execute(ctx=_ctx(None, "session-1"), action="recall", query="anonymous")
    elsewhere = await tool.execute(ctx=_ctx(None, "session-2"), action="recall", query="anonymous")
    assert "anonymous note" in same.content[0].text
    assert "No relevant memories" in elsewhere.content[0].text

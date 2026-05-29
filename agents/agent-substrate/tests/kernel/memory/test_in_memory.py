"""Tests for InMemoryHistoryProvider."""

from __future__ import annotations


from ravi.fabric.memory.in_memory import InMemoryHistoryProvider
from ravi.kernel.messages.client_messages import UserMessage, AssistantMessage

SID = "session-1"


def _user(text: str) -> UserMessage:
    return UserMessage(content=[text])


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(content=[text])


# ══════════════════════════════════════════════════════════════════════════════
# Basic CRUD
# ══════════════════════════════════════════════════════════════════════════════


async def test_empty_session_returns_empty_list():
    mem = InMemoryHistoryProvider()
    assert await mem.load_messages(SID) == []
    assert await mem.count_messages(SID) == 0


async def test_save_and_load_single_message():
    mem = InMemoryHistoryProvider()
    msg = _user("hello")
    written = await mem.save_messages(SID, [msg])
    assert written == 1
    result = await mem.load_messages(SID)
    assert len(result) == 1
    assert result[0] is msg


async def test_messages_preserve_insertion_order():
    mem = InMemoryHistoryProvider()
    msgs = [_user("first"), _assistant("second"), _user("third")]
    await mem.save_messages(SID, msgs)
    stored = await mem.load_messages(SID)
    assert [m.content for m in stored] == [m.content for m in msgs]


async def test_clear_session_empties_only_that_session():
    mem = InMemoryHistoryProvider()
    await mem.save_messages(SID, [_user("hi")])
    await mem.save_messages("other", [_user("keep")])
    await mem.clear_session(SID)
    assert await mem.load_messages(SID) == []
    assert await mem.count_messages("other") == 1


async def test_sessions_are_isolated():
    mem = InMemoryHistoryProvider()
    await mem.save_messages("a", [_user("a-msg")])
    await mem.save_messages("b", [_user("b-msg")])
    assert await mem.count_messages("a") == 1
    assert (await mem.load_messages("b"))[0].content == ["b-msg"]


# ══════════════════════════════════════════════════════════════════════════════
# Limit / slicing
# ══════════════════════════════════════════════════════════════════════════════


async def test_load_with_limit_returns_last_n():
    mem = InMemoryHistoryProvider()
    await mem.save_messages(SID, [_user(f"msg {i}") for i in range(10)])
    last_3 = await mem.load_messages(SID, limit=3)
    assert len(last_3) == 3
    assert last_3[-1].content == ["msg 9"]


async def test_limit_larger_than_count_returns_all():
    mem = InMemoryHistoryProvider()
    await mem.save_messages(SID, [_user("only one")])
    assert len(await mem.load_messages(SID, limit=100)) == 1


async def test_limit_zero_returns_empty():
    mem = InMemoryHistoryProvider()
    await mem.save_messages(SID, [_user("hi")])
    assert await mem.load_messages(SID, limit=0) == []


# ══════════════════════════════════════════════════════════════════════════════
# Isolation: load_messages returns a copy
# ══════════════════════════════════════════════════════════════════════════════


async def test_returned_list_is_a_copy():
    mem = InMemoryHistoryProvider()
    await mem.save_messages(SID, [_user("original")])
    copy = await mem.load_messages(SID)
    copy.append(_user("injected"))
    stored = await mem.load_messages(SID)
    assert len(stored) == 1  # original list unchanged


async def test_save_empty_list_is_noop():
    mem = InMemoryHistoryProvider()
    assert await mem.save_messages(SID, []) == 0
    assert await mem.count_messages(SID) == 0

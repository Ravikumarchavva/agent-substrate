"""Tests for UnboundedMemory."""

from __future__ import annotations


from ravi.kernel.memory.unbounded_memory import UnboundedMemory
from ravi.kernel.messages.client_messages import UserMessage, AssistantMessage


def _user(text: str) -> UserMessage:
    return UserMessage(content=[text])


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(content=[text])


# ══════════════════════════════════════════════════════════════════════════════
# Basic CRUD
# ══════════════════════════════════════════════════════════════════════════════


async def test_empty_memory_returns_empty_list():
    mem = UnboundedMemory()
    msgs = await mem.get_messages()
    assert msgs == []


async def test_add_and_retrieve_single_message():
    mem = UnboundedMemory()
    msg = _user("hello")
    await mem.add_message(msg)
    result = await mem.get_messages()
    assert len(result) == 1
    assert result[0] is msg


async def test_messages_preserve_insertion_order():
    mem = UnboundedMemory()
    msgs = [_user("first"), _assistant("second"), _user("third")]
    for m in msgs:
        await mem.add_message(m)
    stored = await mem.get_messages()
    assert [m.content for m in stored] == [m.content for m in msgs]


async def test_clear_empties_memory():
    mem = UnboundedMemory()
    await mem.add_message(_user("hi"))
    await mem.clear()
    assert await mem.get_messages() == []


# ══════════════════════════════════════════════════════════════════════════════
# Limit / slicing
# ══════════════════════════════════════════════════════════════════════════════


async def test_get_messages_with_limit_returns_last_n():
    mem = UnboundedMemory()
    for i in range(10):
        await mem.add_message(_user(f"msg {i}"))
    last_3 = await mem.get_messages(limit=3)
    assert len(last_3) == 3
    assert last_3[-1].content == ["msg 9"]


async def test_limit_larger_than_count_returns_all():
    mem = UnboundedMemory()
    await mem.add_message(_user("only one"))
    result = await mem.get_messages(limit=100)
    assert len(result) == 1


async def test_limit_zero_returns_empty():
    mem = UnboundedMemory()
    await mem.add_message(_user("hi"))
    result = await mem.get_messages(limit=0)
    assert result == []


# ══════════════════════════════════════════════════════════════════════════════
# Token count heuristic
# ══════════════════════════════════════════════════════════════════════════════


async def test_token_count_increases_with_messages():
    mem = UnboundedMemory()
    before = await mem.get_token_count()
    await mem.add_message(_user("a" * 400))
    after = await mem.get_token_count()
    assert after > before


async def test_token_count_zero_on_empty():
    mem = UnboundedMemory()
    assert await mem.get_token_count() == 0


# ══════════════════════════════════════════════════════════════════════════════
# Isolation: get_messages returns a copy
# ══════════════════════════════════════════════════════════════════════════════


async def test_returned_list_is_a_copy():
    mem = UnboundedMemory()
    await mem.add_message(_user("original"))
    copy = await mem.get_messages()
    copy.append(_user("injected"))
    stored = await mem.get_messages()
    assert len(stored) == 1  # original list unchanged

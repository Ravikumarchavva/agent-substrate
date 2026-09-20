"""Unit tests for MemoryTool with ShortTermMemory and MemoryStore."""

from __future__ import annotations

import pytest

from substrate.capabilities.memory.lance_memory_store import LanceMemoryStore
from substrate.capabilities.tools.memory import MemoryTool
from substrate.kernel.core.identity import Actor


class FakeShortTermMemory:
    def __init__(self) -> None:
        self.state: dict[str, dict[str, str]] = {}

    async def get_state(self, session_id: str) -> dict[str, str]:
        return self.state.get(session_id, {})

    async def update_state(self, session_id: str, patch: dict[str, str]) -> None:
        self.state.setdefault(session_id, {}).update(patch)

    async def clear(self, session_id: str) -> None:
        self.state.pop(session_id, None)


@pytest.fixture
def memory_store(tmp_path) -> LanceMemoryStore:
    return LanceMemoryStore(path=tmp_path / "lance_mem")


@pytest.fixture
def short_term() -> FakeShortTermMemory:
    return FakeShortTermMemory()


async def test_memory_tool_short_term_ops(short_term: FakeShortTermMemory) -> None:
    agent = Actor(type="agent", key="test-agent")
    tool = MemoryTool(agent, "sess-1", short_term=short_term)

    # Set
    res = await tool.execute(action="set", key="user_goal", value="Learn Rust")
    assert not res.is_error
    assert "Stored 'user_goal'" in res.content[0].text

    # Get
    res = await tool.execute(action="get", key="user_goal")
    assert not res.is_error
    assert "user_goal: Learn Rust" in res.content[0].text

    # Clear session
    res = await tool.execute(action="clear_session")
    assert not res.is_error
    assert "Session state cleared" in res.content[0].text

    # Get missing
    res = await tool.execute(action="get", key="user_goal")
    assert not res.is_error
    assert "No value for key 'user_goal'" in res.content[0].text


async def test_memory_tool_long_term_ops(memory_store: LanceMemoryStore) -> None:
    agent = Actor(type="user", key="user-123")
    tool = MemoryTool(agent, "sess-1", long_term=memory_store)

    # Remember
    res = await tool.execute(action="remember", value="User prefers dark theme")
    assert not res.is_error
    mem_id = res.structured_content["memory_id"]
    assert mem_id

    # Recall
    res = await tool.execute(action="recall", query="dark theme")
    assert not res.is_error
    assert "User prefers dark theme" in res.content[0].text
    assert mem_id[:8] in res.content[0].text

    # Recall non-matching
    res = await tool.execute(action="recall", query="light mode")
    assert not res.is_error
    assert "No relevant memories found" in res.content[0].text

    # Forget
    res = await tool.execute(action="forget", memory_id=mem_id)
    assert not res.is_error
    assert f"Deleted memory {mem_id}" in res.content[0].text

    # Forget again (not found)
    res = await tool.execute(action="forget", memory_id=mem_id)
    assert res.is_error
    assert f"Memory {mem_id} not found" in res.content[0].text


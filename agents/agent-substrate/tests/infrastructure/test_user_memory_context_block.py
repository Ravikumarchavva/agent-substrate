"""build_user_memory_context_block() — the <user_context> system-prompt
block, its bounding/gating behavior, and that it's framed as background,
not as an instruction the model is told to obey unconditionally."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from substrate.infrastructure.serving_factory import build_user_memory_context_block


@dataclass
class _FakeMemory:
    content: str
    id: str = "mem-id"
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class _FakeLongTermMemory:
    def __init__(self, memories: list[_FakeMemory]) -> None:
        self._memories = memories
        self.calls: list[tuple[str, str, int]] = []

    async def list_all(self, agent_id, *, namespace="default", limit=20):
        self.calls.append((str(agent_id), namespace, limit))
        return self._memories[:limit]


async def test_returns_empty_string_when_no_long_term_memory_configured():
    assert await build_user_memory_context_block(None, "user-1") == ""


async def test_returns_empty_string_when_no_user_id():
    store = _FakeLongTermMemory([_FakeMemory("some fact")])
    assert await build_user_memory_context_block(store, None) == ""
    assert store.calls == []  # never even queries without a user id


async def test_returns_empty_string_when_user_has_no_memories():
    store = _FakeLongTermMemory([])
    assert await build_user_memory_context_block(store, "user-1") == ""


async def test_builds_a_user_context_block_with_facts():
    store = _FakeLongTermMemory(
        [_FakeMemory("Always answer in French"), _FakeMemory("Prefers concise answers")]
    )
    block = await build_user_memory_context_block(store, "user-1")

    assert block.startswith("<user_context>")
    assert block.endswith("</user_context>")
    assert "<fact>Always answer in French</fact>" in block
    assert "<fact>Prefers concise answers</fact>" in block


async def test_framed_as_background_not_instruction():
    """The whole point of the user's requested design: the block must not
    read as an unconditional directive."""
    store = _FakeLongTermMemory([_FakeMemory("some fact")])
    block = await build_user_memory_context_block(store, "user-1")

    assert "not a command" in block.lower()
    assert "judgment" in block.lower()


async def test_queried_with_preference_namespace_and_user_scoped_agent_id():
    store = _FakeLongTermMemory([_FakeMemory("fact")])
    await build_user_memory_context_block(store, "user-42", limit=5)

    assert len(store.calls) == 1
    agent_id_str, namespace, limit = store.calls[0]
    assert namespace == "preference"
    assert limit == 5
    assert "user-42" in agent_id_str


async def test_limit_is_passed_through_to_bound_the_block():
    memories = [_FakeMemory(f"fact {i}") for i in range(30)]
    store = _FakeLongTermMemory(memories)

    block = await build_user_memory_context_block(store, "user-1", limit=3)

    assert block.count("<fact>") == 3


async def test_escapes_xml_special_characters_in_fact_content():
    store = _FakeLongTermMemory([_FakeMemory('User said <script>&"quote"')])
    block = await build_user_memory_context_block(store, "user-1")

    assert "<script>" not in block
    assert "&lt;script&gt;" in block
    assert "&amp;" in block
    assert "&quot;" in block

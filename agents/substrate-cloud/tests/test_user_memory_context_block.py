"""build_user_memory_context_block() — the <user_context> system-prompt
block, its bounding/gating behavior, whose facts it reads, and that it's framed as
background, not as an instruction the model is told to obey unconditionally."""

from __future__ import annotations

from substrate.stores.memory_tables import Memory

import pytest

from substrate.stores import MemoryNamespace, MemoryRecord
from substrate.stores import Store
from substrate_cloud.factory import build_user_memory_context_block

TENANT = "acme"


@pytest.fixture
def store(tmp_path) -> Memory:
    return Store.at(tmp_path).memory


async def _remember(
    store: Memory, text: str, *, user: str = "user-1", tenant: str = TENANT
) -> None:
    await store.save(
        MemoryRecord.from_text(
            text, namespace=MemoryNamespace(tenant_id=tenant, user_id=user)
        )
    )


async def test_returns_empty_string_when_no_long_term_memory_configured():
    assert await build_user_memory_context_block(None, TENANT, "user-1") == ""


async def test_returns_empty_string_when_no_user_id(store):
    await _remember(store, "some fact")
    assert await build_user_memory_context_block(store, TENANT, None) == ""


async def test_returns_empty_string_when_user_has_no_memories(store):
    assert await build_user_memory_context_block(store, TENANT, "user-1") == ""


async def test_builds_a_user_context_block_with_facts(store):
    await _remember(store, "Always answer in French")
    await _remember(store, "Prefers concise answers")
    block = await build_user_memory_context_block(store, TENANT, "user-1")

    assert block.startswith("<user_context>")
    assert block.endswith("</user_context>")
    assert "<fact>Always answer in French</fact>" in block
    assert "<fact>Prefers concise answers</fact>" in block


async def test_framed_as_background_not_instruction(store):
    """The whole point of the user's requested design: the block must not
    read as an unconditional directive."""
    await _remember(store, "some fact")
    block = await build_user_memory_context_block(store, TENANT, "user-1")

    assert "not a command" in block.lower()
    assert "judgment" in block.lower()


async def test_only_this_users_facts_in_this_tenant_are_included(store):
    """What goes into a system prompt is exactly what this user in this tenant saved —
    another user's, or another tenant's, never."""
    await _remember(store, "mine")
    await _remember(store, "someone else's", user="user-2")
    await _remember(store, "another tenant's", tenant="evilcorp")
    await store.save(
        MemoryRecord.from_text(
            "everyone in acme", namespace=MemoryNamespace(tenant_id=TENANT)
        )
    )

    block = await build_user_memory_context_block(store, TENANT, "user-1")

    assert "<fact>mine</fact>" in block
    assert "<fact>everyone in acme</fact>" in block
    assert "someone else's" not in block
    assert "another tenant's" not in block


async def test_limit_is_passed_through_to_bound_the_block(store):
    for i in range(30):
        await _remember(store, f"fact {i}")

    block = await build_user_memory_context_block(store, TENANT, "user-1", limit=3)

    assert block.count("<fact>") == 3


async def test_escapes_xml_special_characters_in_fact_content(store):
    await _remember(store, 'User said <script>&"quote"')
    block = await build_user_memory_context_block(store, TENANT, "user-1")

    assert "<script>" not in block
    assert "&lt;script&gt;" in block
    assert "&amp;" in block
    assert "&quot;" in block

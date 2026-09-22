"""Unit tests for MemoryManager: extraction, promotion, reconciliation, and branch pruning."""

from __future__ import annotations

import pytest

from substrate.capabilities.memory.lance_memory_store import LanceMemoryStore
from substrate.capabilities.memory.manager import MemoryManager
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.storage.memory import (
    MemoryCategory,
    MemoryNamespace,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryStatus,
)


@pytest.fixture
def store(tmp_path) -> LanceMemoryStore:
    return LanceMemoryStore(path=tmp_path / "mem_manager_store")


@pytest.fixture
def manager(store: LanceMemoryStore) -> MemoryManager:
    return MemoryManager(store)


async def test_extract_candidates_directive_and_semantic(
    manager: MemoryManager,
    store: LanceMemoryStore,
) -> None:
    ns = MemoryNamespace(tenant_id="acme", user_id="user-1")
    prov = MemoryProvenance(source_session_id="s1", source_branch_id="main")

    messages = [
        ChatMessage(role=Role.USER, content=[TextBlock(text="Please always use TypeScript with strict null checks.")]),
        ChatMessage(role=Role.USER, content=[TextBlock(text="The database runs PostgreSQL 16 on port 5432.")]),
    ]

    candidates = await manager.extract_candidates(messages, namespace=ns, provenance=prov)
    assert len(candidates) == 2

    # First is a DIRECTIVE candidate
    assert candidates[0].category == MemoryCategory.DIRECTIVE
    assert candidates[0].status == MemoryStatus.CANDIDATE
    assert "TypeScript" in candidates[0].to_text()

    # Second is a SEMANTIC candidate
    assert candidates[1].category == MemoryCategory.SEMANTIC
    assert candidates[1].status == MemoryStatus.CANDIDATE
    assert "PostgreSQL 16" in candidates[1].to_text()

    # Verify they were saved as CANDIDATE in store
    cand_matches = await store.query(MemoryQuery(namespace=ns, statuses=(MemoryStatus.CANDIDATE,)))
    assert len(cand_matches) == 2

    # Verify no ACTIVE memories exist yet
    active_matches = await store.query(MemoryQuery(namespace=ns, statuses=(MemoryStatus.ACTIVE,)))
    assert len(active_matches) == 0


async def test_promote_candidate(manager: MemoryManager, store: LanceMemoryStore) -> None:
    ns = MemoryNamespace(tenant_id="acme", user_id="user-1")
    cand = MemoryRecord.candidate("User prefers dark mode.", category=MemoryCategory.DIRECTIVE, namespace=ns)
    await store.save(cand)

    # Promote to ACTIVE
    promoted = await manager.promote(cand.id)
    assert promoted is not None
    assert promoted.status == MemoryStatus.ACTIVE
    assert promoted.id == cand.id

    # Verify in store
    rec = await store.get(cand.id)
    assert rec is not None
    assert rec.status == MemoryStatus.ACTIVE


async def test_reconcile_contradiction_supersedes(
    manager: MemoryManager,
    store: LanceMemoryStore,
) -> None:
    ns = MemoryNamespace(tenant_id="acme", user_id="user-1")

    # Initial active memory
    old_record = MemoryRecord.from_text("User is using Python 3.11", namespace=ns)
    await store.save(old_record)

    # New contradictory memory superseding old one
    new_record = MemoryRecord.from_text("User upgraded to Python 3.13", namespace=ns)
    new_id = await manager.reconcile_and_save(new_record, supersedes_id=old_record.id)

    # Old record should now be SUPERSEDED
    old_fetched = await store.get(old_record.id)
    assert old_fetched is not None
    assert old_fetched.status == MemoryStatus.SUPERSEDED

    # New record should have provenance pointing to old_record.id
    new_fetched = await store.get(new_id)
    assert new_fetched is not None
    assert new_fetched.status == MemoryStatus.ACTIVE
    assert new_fetched.provenance.supersedes_id == old_record.id


async def test_discard_branch(manager: MemoryManager, store: LanceMemoryStore) -> None:
    ns = MemoryNamespace(tenant_id="acme", user_id="user-1")

    # Candidate on exploratory branch
    c_branch = MemoryRecord.candidate(
        "Candidate on experimental branch",
        namespace=ns,
        provenance=MemoryProvenance(source_branch_id="exp-branch-1"),
    )
    await store.save(c_branch)

    # Active memory on main branch
    active_main = MemoryRecord.from_text(
        "Active main memory",
        namespace=ns,
        provenance=MemoryProvenance(source_branch_id="main"),
    )
    await store.save(active_main)

    # Abandon experimental branch
    discarded = await manager.discard_branch("exp-branch-1", namespace=ns)
    assert discarded == 1

    # Candidate should be gone
    assert await store.get(c_branch.id) is None

    # Main active memory remains
    assert await store.get(active_main.id) is not None


"""Tests for Phase 1A: History DAG, Branching, and Concurrency Invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from substrate.agents.storage.history import DefaultHistoryResolver, InMemoryHistoryProvider
from substrate.kernel.core.content import ChatMessage, TextBlock
from substrate.kernel.exceptions import (
    BranchAlreadyExistsError,
    BranchHeadConflictError,
    BranchNotFoundError,
    DAGIntegrityError,
)
from substrate.kernel.storage.history import Branch, MessageNode


def _msg(text: str, role: str = "user") -> ChatMessage:
    return ChatMessage(role=role, content=[TextBlock(text=text)])


# ---------------------------------------------------------------------------
# 1. Node Immutability & Model Integrity
# ---------------------------------------------------------------------------


def test_message_node_immutability():
    node = MessageNode(
        id="n1",
        session_id="s1",
        payload=_msg("hello"),
    )
    with pytest.raises(ValidationError):
        node.run_id = "r2"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        node.parent_id = "p1"  # type: ignore[misc]


def test_branch_immutability():
    branch = Branch(
        id="main",
        session_id="s1",
        head_message_id="n1",
        version=1,
    )
    with pytest.raises(ValidationError):
        branch.version = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Node Persistence Invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_node_root_and_child():
    provider = InMemoryHistoryProvider()
    root = MessageNode(id="root", session_id="s1", payload=_msg("root"))
    await provider.append_node(root)

    child = MessageNode(
        id="c1", parent_id="root", session_id="s1", payload=_msg("child")
    )
    await provider.append_node(child)

    fetched_root = await provider.get_node("root")
    assert fetched_root is not None
    assert fetched_root.id == "root"
    assert fetched_root.parent_id is None

    fetched_child = await provider.get_node("c1")
    assert fetched_child is not None
    assert fetched_child.parent_id == "root"


@pytest.mark.asyncio
async def test_append_node_self_loop_rejected():
    provider = InMemoryHistoryProvider()
    loop_node = MessageNode(id="self1", parent_id="self1", session_id="s1", payload=_msg("loop"))
    with pytest.raises(DAGIntegrityError, match="cannot have itself as parent"):
        await provider.append_node(loop_node)


@pytest.mark.asyncio
async def test_append_node_missing_parent_rejected():
    provider = InMemoryHistoryProvider()
    orphan = MessageNode(id="c1", parent_id="nonexistent", session_id="s1", payload=_msg("orphan"))
    with pytest.raises(DAGIntegrityError, match="Parent node 'nonexistent' does not exist"):
        await provider.append_node(orphan)


@pytest.mark.asyncio
async def test_append_node_cross_session_rejected():
    provider = InMemoryHistoryProvider()
    root_s1 = MessageNode(id="root1", session_id="s1", payload=_msg("root 1"))
    await provider.append_node(root_s1)

    cross_node = MessageNode(
        id="c2", parent_id="root1", session_id="s2", payload=_msg("cross session")
    )
    with pytest.raises(DAGIntegrityError, match="Parent node belongs to session 's1', expected 's2'"):
        await provider.append_node(cross_node)


@pytest.mark.asyncio
async def test_append_node_idempotency():
    provider = InMemoryHistoryProvider()
    node = MessageNode(id="n1", session_id="s1", run_id="r1", payload=_msg("msg 1"))
    await provider.append_node(node)

    # Identical node append is an idempotent no-op
    identical_node = MessageNode(id="n1", session_id="s1", run_id="r1", payload=_msg("msg 1"))
    await provider.append_node(identical_node)

    # Differing payload on same ID is rejected
    diff_node = MessageNode(id="n1", session_id="s1", run_id="r1", payload=_msg("diff msg"))
    with pytest.raises(DAGIntegrityError, match="already exists with different contents"):
        await provider.append_node(diff_node)


# ---------------------------------------------------------------------------
# 3. Ancestry Resolution (DefaultHistoryResolver)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_ancestry_linear():
    provider = InMemoryHistoryProvider()
    n0 = MessageNode(id="n0", session_id="s1", payload=_msg("turn 0"))
    n1 = MessageNode(id="n1", parent_id="n0", session_id="s1", payload=_msg("turn 1"))
    n2 = MessageNode(id="n2", parent_id="n1", session_id="s1", payload=_msg("turn 2"))
    await provider.append_node(n0)
    await provider.append_node(n1)
    await provider.append_node(n2)

    resolver = DefaultHistoryResolver(provider)
    chain = await resolver.resolve_ancestry("n2")
    assert [n.id for n in chain] == ["n0", "n1", "n2"]


@pytest.mark.asyncio
async def test_resolve_ancestry_with_stop_at_node():
    provider = InMemoryHistoryProvider()
    n0 = MessageNode(id="n0", session_id="s1", payload=_msg("turn 0"))
    n1 = MessageNode(id="n1", parent_id="n0", session_id="s1", payload=_msg("turn 1"))
    n2 = MessageNode(id="n2", parent_id="n1", session_id="s1", payload=_msg("turn 2"))
    await provider.append_node(n0)
    await provider.append_node(n1)
    await provider.append_node(n2)

    resolver = DefaultHistoryResolver(provider)
    # Stop at n0 -> returns delta after n0 up to n2
    chain = await resolver.resolve_ancestry("n2", stop_at_node_id="n0")
    assert [n.id for n in chain] == ["n1", "n2"]


# ---------------------------------------------------------------------------
# 4. Branching & Forking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fork_empty_branch():
    provider = InMemoryHistoryProvider()
    await provider.ensure_branch("s1", "main")

    forked = await provider.fork_branch("s1", "main", "feature")
    assert forked.id == "feature"
    assert forked.head_message_id is None
    assert forked.forked_from_message_id is None

    # Forking empty branch with specific node raises DAGIntegrityError
    with pytest.raises(DAGIntegrityError, match="Cannot fork from node 'n1' on empty branch"):
        await provider.fork_branch("s1", "main", "feature2", fork_from_message_id="n1")


@pytest.mark.asyncio
async def test_fork_branch_non_empty_default_head():
    provider = InMemoryHistoryProvider()
    n0 = MessageNode(id="n0", session_id="s1", payload=_msg("turn 0"))
    await provider.append_node(n0)
    await provider.ensure_branch("s1", "main", head_message_id="n0")

    forked = await provider.fork_branch("s1", "main", "exp1")
    assert forked.id == "exp1"
    assert forked.head_message_id == "n0"
    assert forked.forked_from_message_id == "n0"


@pytest.mark.asyncio
async def test_fork_branch_from_specific_ancestor():
    provider = InMemoryHistoryProvider()
    n0 = MessageNode(id="n0", session_id="s1", payload=_msg("turn 0"))
    n1 = MessageNode(id="n1", parent_id="n0", session_id="s1", payload=_msg("turn 1"))
    n2 = MessageNode(id="n2", parent_id="n1", session_id="s1", payload=_msg("turn 2"))
    await provider.append_node(n0)
    await provider.append_node(n1)
    await provider.append_node(n2)
    await provider.ensure_branch("s1", "main", head_message_id="n2")

    # Fork from n1 (ancestor of n2)
    forked = await provider.fork_branch(
        "s1", "main", "branch-from-turn1", fork_from_message_id="n1"
    )
    assert forked.id == "branch-from-turn1"
    assert forked.head_message_id == "n1"
    assert forked.forked_from_message_id == "n1"


@pytest.mark.asyncio
async def test_fork_branch_invalid_ancestor_rejected():
    provider = InMemoryHistoryProvider()
    n0 = MessageNode(id="n0", session_id="s1", payload=_msg("turn 0"))
    n1 = MessageNode(id="n1", parent_id="n0", session_id="s1", payload=_msg("turn 1"))
    # Unrelated node
    other = MessageNode(id="other", session_id="s1", payload=_msg("other"))
    await provider.append_node(n0)
    await provider.append_node(n1)
    await provider.append_node(other)
    await provider.ensure_branch("s1", "main", head_message_id="n1")

    # Trying to fork main at 'other' (which is not an ancestor of n1)
    with pytest.raises(DAGIntegrityError, match="is not an ancestor of source branch head"):
        await provider.fork_branch("s1", "main", "bad-branch", fork_from_message_id="other")


@pytest.mark.asyncio
async def test_fork_branch_errors():
    provider = InMemoryHistoryProvider()
    await provider.ensure_branch("s1", "main")

    # Source branch not found
    with pytest.raises(BranchNotFoundError):
        await provider.fork_branch("s1", "nonexistent", "new_b")

    # Target branch already exists
    with pytest.raises(BranchAlreadyExistsError):
        await provider.fork_branch("s1", "main", "main")


# ---------------------------------------------------------------------------
# 5. Branch Head Advancement & Concurrency (set_branch_head)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_branch_head_cas_success():
    provider = InMemoryHistoryProvider()
    n0 = MessageNode(id="n0", session_id="s1", payload=_msg("turn 0"))
    n1 = MessageNode(id="n1", parent_id="n0", session_id="s1", payload=_msg("turn 1"))
    await provider.append_node(n0)
    await provider.append_node(n1)

    branch = await provider.ensure_branch("s1", "main", head_message_id="n0")
    assert branch.version == 0

    # Advance head with matching expected_head_id and version
    updated = await provider.set_branch_head(
        "s1", "main", "n1", expected_head_id="n0", expected_version=0
    )
    assert updated.head_message_id == "n1"
    assert updated.version == 1


@pytest.mark.asyncio
async def test_set_branch_head_cas_conflicts():
    provider = InMemoryHistoryProvider()
    n0 = MessageNode(id="n0", session_id="s1", payload=_msg("turn 0"))
    n1 = MessageNode(id="n1", parent_id="n0", session_id="s1", payload=_msg("turn 1"))
    await provider.append_node(n0)
    await provider.append_node(n1)

    await provider.ensure_branch("s1", "main", head_message_id="n0")

    # Head mismatch conflict
    with pytest.raises(BranchHeadConflictError, match="Branch head conflict"):
        await provider.set_branch_head("s1", "main", "n1", expected_head_id="wrong_head")

    # Version mismatch conflict
    with pytest.raises(BranchHeadConflictError, match="Branch version conflict"):
        await provider.set_branch_head("s1", "main", "n1", expected_version=99)


# ---------------------------------------------------------------------------
# 6. Atomic append_and_advance (Reviewer Critical Item #1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_and_advance_success_chain():
    provider = InMemoryHistoryProvider()

    # Turn 0 on empty branch (parent_id must be None)
    n0 = MessageNode(id="n0", session_id="s1", parent_id=None, payload=_msg("turn 0"))
    b0 = await provider.append_and_advance(n0, "main", expected_head_id=None)
    assert b0.head_message_id == "n0"
    assert b0.version == 1

    # Turn 1: parent_id must match current head ("n0")
    n1 = MessageNode(id="n1", session_id="s1", parent_id="n0", payload=_msg("turn 1"))
    b1 = await provider.append_and_advance(n1, "main", expected_head_id="n0", expected_version=1)
    assert b1.head_message_id == "n1"
    assert b1.version == 2

    # Verify nodes persisted
    resolver = DefaultHistoryResolver(provider)
    chain = await resolver.resolve_ancestry(b1.head_message_id)
    assert [n.id for n in chain] == ["n0", "n1"]


@pytest.mark.asyncio
async def test_append_and_advance_parent_head_mismatch_rejected():
    """Reviewer Rule 1: node.parent_id must equal current branch head."""
    provider = InMemoryHistoryProvider()

    n0 = MessageNode(id="n0", session_id="s1", parent_id=None, payload=_msg("turn 0"))
    await provider.append_and_advance(n0, "main")

    # Attempting to append node with parent_id="unrelated" or None onto branch whose head is "n0"
    rogue_node = MessageNode(
        id="rogue", session_id="s1", parent_id=None, payload=_msg("rogue")
    )
    with pytest.raises(
        BranchHeadConflictError,
        match="does not match current head 'n0'",
    ):
        await provider.append_and_advance(rogue_node, "main")


@pytest.mark.asyncio
async def test_independent_branch_advancement():
    """Verify branching isolation: advancing branch B does not affect branch A."""
    provider = InMemoryHistoryProvider()

    # Root turn on main
    n0 = MessageNode(id="n0", session_id="s1", parent_id=None, payload=_msg("root"))
    await provider.append_and_advance(n0, "main")

    # Fork feature branch at n0
    await provider.fork_branch("s1", "main", "feature")

    # Advance main with n1_main
    n1_main = MessageNode(
        id="n1_main", session_id="s1", parent_id="n0", payload=_msg("main 1")
    )
    b_main = await provider.append_and_advance(n1_main, "main")

    # Advance feature with n1_feat (also parented on n0)
    n1_feat = MessageNode(
        id="n1_feat", session_id="s1", parent_id="n0", payload=_msg("feature 1")
    )
    b_feat = await provider.append_and_advance(n1_feat, "feature")

    # Check branch heads
    assert b_main.head_message_id == "n1_main"
    assert b_feat.head_message_id == "n1_feat"

    # Resolve ancestry for each branch
    resolver = DefaultHistoryResolver(provider)
    main_chain = await resolver.resolve_ancestry(b_main.head_message_id)
    feat_chain = await resolver.resolve_ancestry(b_feat.head_message_id)

    assert [n.id for n in main_chain] == ["n0", "n1_main"]
    assert [n.id for n in feat_chain] == ["n0", "n1_feat"]


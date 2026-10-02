"""Tests for Phase 1A: History DAG, Branching, and Concurrency Invariants."""

from __future__ import annotations

from tests._stores import fs_history

import pytest

from substrate.context import DefaultHistoryResolver
from substrate.types import ChatMessage, TextBlock
from substrate.stores import MessageNode


def _msg(text: str, role: str = "user") -> ChatMessage:
    return ChatMessage(role=role, content=[TextBlock(text=text)])


# ---------------------------------------------------------------------------
# 1. Node Immutability & Model Integrity
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 2. Node Persistence Invariants
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3. Ancestry Resolution (DefaultHistoryResolver)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_ancestry_linear():
    provider = fs_history()
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
    provider = fs_history()
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


# ---------------------------------------------------------------------------
# 5. Branch Head Advancement & Concurrency (set_branch_head)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 6. Atomic append_and_advance (Reviewer Critical Item #1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_independent_branch_advancement():
    """Verify branching isolation: advancing branch B does not affect branch A."""
    provider = fs_history()

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

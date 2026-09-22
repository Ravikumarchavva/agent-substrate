"""Tests for HistoryCheckpoint, CheckpointResolver, and ContextBuilder checkpoint integration."""

from datetime import datetime, timezone
import pytest

from substrate.agents.context.builder import DefaultContextBuilder
from substrate.agents.storage.history import (
    AncestryCheckpointResolver,
    InMemoryHistoryProvider,
)
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.exceptions import DAGIntegrityError
from substrate.kernel.storage.history import HistoryCheckpoint, MessageNode


def _msg(text: str, role: Role = Role.USER) -> ChatMessage:
    return ChatMessage(role=role, content=[TextBlock(text=text)])


def _node(
    node_id: str,
    parent_id: str | None,
    text: str,
    session_id: str = "sess-1",
    role: Role = Role.USER,
) -> MessageNode:
    return MessageNode(
        id=node_id,
        parent_id=parent_id,
        session_id=session_id,
        payload=_msg(text, role),
    )


class TestCheckpointModel:
    def test_checkpoint_immutability(self) -> None:
        cp = HistoryCheckpoint(
            session_id="sess-1",
            anchor_message_id="msg-1",
            summary="Test summary",
        )
        assert cp.id is not None
        assert cp.created_at.tzinfo == timezone.utc
        assert cp.state == {}
        with pytest.raises(Exception):
            cp.summary = "New summary"  # type: ignore[misc]


class TestCheckpointStorage:
    @pytest.mark.asyncio
    async def test_save_requires_existing_anchor(self) -> None:
        provider = InMemoryHistoryProvider()
        cp = HistoryCheckpoint(
            session_id="sess-1",
            anchor_message_id="non-existent",
            summary="summary",
        )
        with pytest.raises(DAGIntegrityError, match="does not exist"):
            await provider.save_checkpoint(cp)

    @pytest.mark.asyncio
    async def test_save_requires_same_session_anchor(self) -> None:
        provider = InMemoryHistoryProvider()
        node = _node("node-1", None, "hello", session_id="sess-1")
        await provider.append_node(node)

        cp = HistoryCheckpoint(
            session_id="sess-2",  # Different session
            anchor_message_id="node-1",
            summary="summary",
        )
        with pytest.raises(DAGIntegrityError, match="belongs to session"):
            await provider.save_checkpoint(cp)

    @pytest.mark.asyncio
    async def test_get_and_list_checkpoints(self) -> None:
        provider = InMemoryHistoryProvider()
        node1 = _node("n1", None, "first", session_id="s1")
        node2 = _node("n2", None, "other", session_id="s2")
        await provider.append_node(node1)
        await provider.append_node(node2)

        cp1 = HistoryCheckpoint(session_id="s1", anchor_message_id="n1", summary="sum1")
        cp2 = HistoryCheckpoint(session_id="s2", anchor_message_id="n2", summary="sum2")
        await provider.save_checkpoint(cp1)
        await provider.save_checkpoint(cp2)

        assert await provider.get_checkpoint(cp1.id) == cp1
        assert await provider.get_checkpoint("non-existent") is None

        s1_cps = await provider.list_checkpoints("s1")
        assert len(s1_cps) == 1
        assert s1_cps[0].id == cp1.id

        s2_cps = await provider.list_checkpoints("s2")
        assert len(s2_cps) == 1
        assert s2_cps[0].id == cp2.id


class TestAncestryCheckpointResolver:
    @pytest.mark.asyncio
    async def test_shared_ancestor_checkpoint_valid_for_descendants(self) -> None:
        provider = InMemoryHistoryProvider()
        resolver = AncestryCheckpointResolver(provider)

        # Root -> N1 -> N2 on main
        n1 = _node("n1", None, "root")
        n2 = _node("n2", "n1", "step 2")
        await provider.append_and_advance(n1, "main")
        await provider.append_and_advance(n2, "main", expected_head_id="n1")

        # Save checkpoint at N2
        cp_shared = HistoryCheckpoint(
            session_id="sess-1",
            anchor_message_id="n2",
            summary="Summary up to N2",
        )
        await provider.save_checkpoint(cp_shared)

        # Fork branch-a and branch-b from main at N2
        await provider.fork_branch("sess-1", "main", "branch-a")
        await provider.fork_branch("sess-1", "main", "branch-b")

        # Append N3A on branch-a
        n3a = _node("n3a", "n2", "branch a step 1")
        await provider.append_and_advance(n3a, "branch-a", expected_head_id="n2")

        # Append N3B on branch-b
        n3b = _node("n3b", "n2", "branch b step 1")
        await provider.append_and_advance(n3b, "branch-b", expected_head_id="n2")

        # Both branches resolve cp_shared as applicable
        found_a = await resolver.find_applicable_checkpoint("n3a")
        assert found_a is not None
        assert found_a.id == cp_shared.id

        found_b = await resolver.find_applicable_checkpoint("n3b")
        assert found_b is not None
        assert found_b.id == cp_shared.id

    @pytest.mark.asyncio
    async def test_divergent_sibling_checkpoint_rejected(self) -> None:
        provider = InMemoryHistoryProvider()
        resolver = AncestryCheckpointResolver(provider)

        n1 = _node("n1", None, "root")
        n2 = _node("n2", "n1", "step 2")
        await provider.append_and_advance(n1, "main")
        await provider.append_and_advance(n2, "main", expected_head_id="n1")

        cp_shared = HistoryCheckpoint(
            session_id="sess-1", anchor_message_id="n2", summary="Shared N2"
        )
        await provider.save_checkpoint(cp_shared)

        await provider.fork_branch("sess-1", "main", "branch-a")
        await provider.fork_branch("sess-1", "main", "branch-b")

        n3a = _node("n3a", "n2", "branch a step 1")
        await provider.append_and_advance(n3a, "branch-a", expected_head_id="n2")
        n4a = _node("n4a", "n3a", "branch a step 2")
        await provider.append_and_advance(n4a, "branch-a", expected_head_id="n3a")

        # Save checkpoint on branch-a at n4a
        cp_branch_a = HistoryCheckpoint(
            session_id="sess-1", anchor_message_id="n4a", summary="Summary at N4A"
        )
        await provider.save_checkpoint(cp_branch_a)

        # Branch-b advances with n3b
        n3b = _node("n3b", "n2", "branch b step 1")
        await provider.append_and_advance(n3b, "branch-b", expected_head_id="n2")

        # Branch-a resolves cp_branch_a
        found_a = await resolver.find_applicable_checkpoint("n4a")
        assert found_a is not None
        assert found_a.id == cp_branch_a.id

        # Branch-b must NOT resolve cp_branch_a; it resolves cp_shared
        found_b = await resolver.find_applicable_checkpoint("n3b")
        assert found_b is not None
        assert found_b.id == cp_shared.id

    @pytest.mark.asyncio
    async def test_nearest_anchor_chosen_by_topological_depth(self) -> None:
        provider = InMemoryHistoryProvider()
        resolver = AncestryCheckpointResolver(provider)

        n1 = _node("n1", None, "root")
        n2 = _node("n2", "n1", "step 2")
        n3 = _node("n3", "n2", "step 3")
        n4 = _node("n4", "n3", "step 4")
        await provider.append_and_advance(n1, "main")
        await provider.append_and_advance(n2, "main", expected_head_id="n1")
        await provider.append_and_advance(n3, "main", expected_head_id="n2")
        await provider.append_and_advance(n4, "main", expected_head_id="n3")

        # Anchor at depth 2 (N2) created later
        cp_shallow = HistoryCheckpoint(
            session_id="sess-1",
            anchor_message_id="n2",
            summary="N2 summary",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        # Anchor at depth 3 (N3) created earlier
        cp_deep = HistoryCheckpoint(
            session_id="sess-1",
            anchor_message_id="n3",
            summary="N3 summary",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        await provider.save_checkpoint(cp_shallow)
        await provider.save_checkpoint(cp_deep)

        # Resolving for n4 should select cp_deep (depth 3) despite older timestamp
        found = await resolver.find_applicable_checkpoint("n4")
        assert found is not None
        assert found.id == cp_deep.id

    @pytest.mark.asyncio
    async def test_leaf_anchor_checkpoint_applicable(self) -> None:
        provider = InMemoryHistoryProvider()
        resolver = AncestryCheckpointResolver(provider)

        n1 = _node("n1", None, "root")
        await provider.append_and_advance(n1, "main")

        cp = HistoryCheckpoint(
            session_id="sess-1", anchor_message_id="n1", summary="At leaf"
        )
        await provider.save_checkpoint(cp)

        found = await resolver.find_applicable_checkpoint("n1")
        assert found is not None
        assert found.id == cp.id

    @pytest.mark.asyncio
    async def test_no_applicable_checkpoint_returns_none(self) -> None:
        provider = InMemoryHistoryProvider()
        resolver = AncestryCheckpointResolver(provider)

        n1 = _node("n1", None, "root")
        await provider.append_and_advance(n1, "main")

        found = await resolver.find_applicable_checkpoint("n1")
        assert found is None

    @pytest.mark.asyncio
    async def test_non_existent_leaf_raises_error(self) -> None:
        provider = InMemoryHistoryProvider()
        resolver = AncestryCheckpointResolver(provider)

        with pytest.raises(DAGIntegrityError, match="not found"):
            await resolver.find_applicable_checkpoint("ghost")


class TestContextBuilderCheckpointIntegration:
    @pytest.mark.asyncio
    async def test_builder_splices_checkpoint_summary_and_deltas(self) -> None:
        builder = DefaultContextBuilder()

        n1 = _node("n1", None, "msg 1")
        n2 = _node("n2", "n1", "msg 2")
        n3 = _node("n3", "n2", "msg 3")
        n4 = _node("n4", "n3", "msg 4")

        cp = HistoryCheckpoint(
            session_id="sess-1",
            anchor_message_id="n2",
            summary="Compact summary up to n2",
        )

        window = await builder.build(
            [n1, n2, n3, n4],
            checkpoint=cp,
            system_instruction="You are an assistant.",
        )

        assert window.leaf_node_id == "n4"
        assert window.checkpoint_id == cp.id
        assert len(window.messages) == 4

        # Message 0: System prompt
        assert window.messages[0].role == Role.SYSTEM
        assert window.messages[0].text == "You are an assistant."

        # Message 1: Checkpoint summary
        assert window.messages[1].role == Role.USER
        assert "[Conversation Summary]" in window.messages[1].text
        assert "Compact summary up to n2" in window.messages[1].text

        # Message 2 & 3: Delta messages n3 and n4
        assert window.messages[2].text == "msg 3"
        assert window.messages[3].text == "msg 4"


"""Conformance suite for ``HistoryProvider``.

Every implementation — in-memory, local filesystem, Postgres, any a consumer writes — runs exactly
these tests: the DAG's integrity rules, optimistic concurrency on branch heads, fork semantics, and
that one session's data never touches another's. Subclass it and provide the ``store`` fixture.
"""

from __future__ import annotations

import pytest

from substrate.types.content import ChatMessage, Role
from substrate.types.errors import BranchAlreadyExistsError, BranchHeadConflictError, BranchNotFoundError
from substrate.types.ids import new_id
from substrate.stores.threads import HistoryCheckpoint, HistoryProvider, MessageNode


def node(session: str, text: str, parent: MessageNode | None = None) -> MessageNode:
    return MessageNode(
        id=new_id(),
        session_id=session,
        parent_id=parent.id if parent else None,
        payload=ChatMessage(role=Role.USER, content=text),
    )


_SESSIONS = ("s", "t", "other", "mine", "theirs", "a", "b", "bystander", "x' OR '1'='1", "a'; DROP TABLE t; --", "../../etc/passwd", "a/b\\c", "..", "ünï-çødé")


class HistoryProviderConformance:
    @pytest.fixture
    async def store(self) -> HistoryProvider:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    @pytest.fixture(autouse=True)
    async def _clean_slate(self, store: HistoryProvider) -> None:
        """Tests name their sessions, so a store that outlives a test (a database) must not carry
        one test's sessions into the next."""
        for session in _SESSIONS:
            await store.delete_session(session)

    async def chain(self, store: HistoryProvider, session: str, n: int, branch: str = "main") -> list[MessageNode]:
        await store.ensure_branch(session, branch)
        nodes: list[MessageNode] = []
        for i in range(n):
            nodes.append(node(session, f"m{i}", nodes[-1] if nodes else None))
            await store.append_and_advance(nodes[-1], branch)
        return nodes

    # ==================================================================== nodes

    async def test_a_node_round_trips(self, store: HistoryProvider) -> None:
        n = node("s", "hello")
        await store.append_node(n)
        got = await store.get_node(n.id)
        assert got is not None and got.id == n.id and got.session_id == "s" and got.payload.text == "hello"

    async def test_a_missing_node_is_none(self, store: HistoryProvider) -> None:
        assert await store.get_node("nope") is None

    async def test_appending_the_same_node_twice_is_idempotent(self, store: HistoryProvider) -> None:
        n = node("s", "x")
        await store.append_node(n)
        await store.append_node(n)
        assert (await store.get_node(n.id)).payload.text == "x"

    async def test_a_node_cannot_be_overwritten_with_different_content(self, store: HistoryProvider) -> None:
        n = node("s", "original")
        await store.append_node(n)
        with pytest.raises(ValueError):
            await store.append_node(n.model_copy(update={"payload": ChatMessage(role=Role.USER, content="forged")}))
        assert (await store.get_node(n.id)).payload.text == "original"

    async def test_a_node_cannot_be_its_own_parent(self, store: HistoryProvider) -> None:
        n = node("s", "x")
        with pytest.raises(ValueError):
            await store.append_node(n.model_copy(update={"parent_id": n.id}))

    async def test_a_node_needs_an_existing_parent_in_the_same_session(self, store: HistoryProvider) -> None:
        other = node("other", "elsewhere")
        await store.append_node(other)
        with pytest.raises(ValueError):
            await store.append_node(node("s", "orphan", node("s", "never stored")))
        with pytest.raises(ValueError):
            await store.append_node(node("s", "cross-session", other))

    # ==================================================================== branches

    async def test_ensure_branch_creates_once_and_returns_the_same_branch(self, store: HistoryProvider) -> None:
        first = await store.ensure_branch("s", "main")
        again = await store.ensure_branch("s", "main")
        assert first.id == again.id == "main" and first.head_message_id is None
        assert [b.id for b in await store.list_branches("s")] == ["main"]

    async def test_a_missing_branch_is_none(self, store: HistoryProvider) -> None:
        assert await store.get_branch("s", "nope") is None

    async def test_append_and_advance_moves_the_head_and_bumps_the_version(self, store: HistoryProvider) -> None:
        nodes = await self.chain(store, "s", 3)
        branch = await store.get_branch("s", "main")
        assert branch.head_message_id == nodes[-1].id
        assert branch.version >= 3

    async def test_append_and_advance_requires_the_node_to_extend_the_head(self, store: HistoryProvider) -> None:
        nodes = await self.chain(store, "s", 2)
        stale = node("s", "branches off the first", nodes[0])
        with pytest.raises(BranchHeadConflictError):
            await store.append_and_advance(stale, "main")
        assert (await store.get_branch("s", "main")).head_message_id == nodes[-1].id

    async def test_a_stale_expected_head_is_a_conflict(self, store: HistoryProvider) -> None:
        nodes = await self.chain(store, "s", 2)
        with pytest.raises(BranchHeadConflictError):
            await store.append_and_advance(node("s", "late", nodes[-1]), "main", expected_head_id=nodes[0].id)
        with pytest.raises(BranchHeadConflictError):
            await store.append_and_advance(node("s", "late", nodes[-1]), "main", expected_version=0)

    async def test_set_branch_head_is_compare_and_set(self, store: HistoryProvider) -> None:
        nodes = await self.chain(store, "s", 3)
        moved = await store.set_branch_head("s", "main", nodes[0].id, expected_head_id=nodes[-1].id)
        assert moved.head_message_id == nodes[0].id
        with pytest.raises(BranchHeadConflictError):
            await store.set_branch_head("s", "main", nodes[1].id, expected_head_id=nodes[-1].id)

    async def test_forking_copies_the_head_pointer(self, store: HistoryProvider) -> None:
        nodes = await self.chain(store, "s", 2)
        fork = await store.fork_branch("s", "main", "exp")
        assert fork.head_message_id == nodes[-1].id
        extended = await store.append_and_advance(node("s", "only on exp", nodes[-1]), "exp")
        assert (await store.get_branch("s", "main")).head_message_id == nodes[-1].id
        assert extended.head_message_id != nodes[-1].id

    async def test_forking_from_an_ancestor_starts_there(self, store: HistoryProvider) -> None:
        nodes = await self.chain(store, "s", 3)
        fork = await store.fork_branch("s", "main", "back", fork_from_message_id=nodes[0].id)
        assert fork.head_message_id == nodes[0].id and fork.forked_from_message_id == nodes[0].id

    async def test_forking_from_a_node_that_is_not_an_ancestor_is_refused(self, store: HistoryProvider) -> None:
        await self.chain(store, "s", 2)
        stranger = node("s", "off to the side")
        await store.append_node(stranger)
        with pytest.raises(ValueError):
            await store.fork_branch("s", "main", "bad", fork_from_message_id=stranger.id)
        assert await store.get_branch("s", "bad") is None

    async def test_forking_needs_a_source_and_an_unused_name(self, store: HistoryProvider) -> None:
        await self.chain(store, "s", 1)
        with pytest.raises(BranchNotFoundError):
            await store.fork_branch("s", "no-such", "x")
        await store.fork_branch("s", "main", "taken")
        with pytest.raises(BranchAlreadyExistsError):
            await store.fork_branch("s", "main", "taken")

    async def test_renaming_changes_the_display_name_only(self, store: HistoryProvider) -> None:
        await store.ensure_branch("s", "main")
        renamed = await store.rename_branch("s", "main", "Primary")
        assert renamed.name == "Primary" and renamed.id == "main"
        with pytest.raises(BranchNotFoundError):
            await store.rename_branch("s", "no-such", "x")

    async def test_deleting_a_branch_keeps_its_nodes(self, store: HistoryProvider) -> None:
        nodes = await self.chain(store, "s", 2)
        await store.fork_branch("s", "main", "tmp")
        await store.delete_branch("s", "tmp")
        assert await store.get_branch("s", "tmp") is None
        assert await store.get_node(nodes[-1].id) is not None

    async def test_the_main_branch_cannot_be_deleted_and_unknown_ones_are_a_no_op(self, store: HistoryProvider) -> None:
        await store.ensure_branch("s", "main")
        with pytest.raises(ValueError):
            await store.delete_branch("s", "main")
        await store.delete_branch("s", "never-existed")

    # ==================================================================== checkpoints

    async def test_checkpoints_round_trip_and_list_per_session(self, store: HistoryProvider) -> None:
        nodes = await self.chain(store, "s", 2)
        other = await self.chain(store, "t", 1)
        cp = HistoryCheckpoint(session_id="s", anchor_message_id=nodes[0].id, summary="so far", state={"k": 1})
        await store.save_checkpoint(cp)
        await store.save_checkpoint(HistoryCheckpoint(session_id="t", anchor_message_id=other[0].id, summary="other"))
        got = await store.get_checkpoint(cp.id)
        assert got is not None and got.summary == "so far" and got.state == {"k": 1}
        assert [c.id for c in await store.list_checkpoints("s")] == [cp.id]
        assert await store.get_checkpoint("nope") is None

    # ==================================================================== sessions

    async def test_sessions_are_isolated_and_deleting_one_leaves_the_other(self, store: HistoryProvider) -> None:
        mine = await self.chain(store, "mine", 2)
        theirs = await self.chain(store, "theirs", 2)
        await store.save_checkpoint(HistoryCheckpoint(session_id="mine", anchor_message_id=mine[0].id, summary="s"))
        await store.delete_session("mine")
        assert await store.get_node(mine[0].id) is None and await store.list_branches("mine") == []
        assert await store.list_checkpoints("mine") == []
        assert await store.get_node(theirs[0].id) is not None and len(await store.list_branches("theirs")) == 1
        await store.delete_session("mine")  # idempotent
        await store.delete_session("never-existed")

    async def test_the_same_branch_name_in_two_sessions_is_two_branches(self, store: HistoryProvider) -> None:
        a = await self.chain(store, "a", 1)
        b = await self.chain(store, "b", 2)
        assert (await store.get_branch("a", "main")).head_message_id == a[-1].id
        assert (await store.get_branch("b", "main")).head_message_id == b[-1].id

    @pytest.mark.parametrize("hostile", ["x' OR '1'='1", "a'; DROP TABLE t; --", "../../etc/passwd", "a/b\\c", "..", "ünï-çødé"])
    async def test_a_hostile_session_or_branch_name_is_inert(self, store: HistoryProvider, hostile: str) -> None:
        bystander = await self.chain(store, "bystander", 1)
        mine = await self.chain(store, hostile, 2, branch="main")
        await store.fork_branch(hostile, "main", hostile)
        assert (await store.get_branch(hostile, hostile)) is not None
        await store.delete_session(hostile)
        assert await store.list_branches(hostile) == []
        assert await store.get_node(bystander[0].id) is not None
        assert mine  # silence unused


__all__ = ["HistoryProviderConformance", "node"]

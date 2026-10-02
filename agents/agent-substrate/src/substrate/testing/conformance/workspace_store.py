"""Conformance suite for ``WorkspaceStore``.

Every implementation — the store's, Postgres, any a consumer writes — runs exactly these tests: a branch's head moves
only by compare-and-swap (so two writers extending one head never both win), a branch is created once, branches never
see each other's heads, and a hostile session or branch name is data. Subclass it and provide the ``store`` fixture.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from substrate.types.errors import SnapshotConflictError
from substrate.workspace.protocols import WorkspaceSnapshot, WorkspaceStore


def snapshot(
    session: str, branch: str, parent: str | None = None, ref: str = "m"
) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        session_id=session,
        branch_id=branch,
        parent_snapshot_id=parent,
        manifest_ref=ref,
    )


class WorkspaceStoreConformance:
    @pytest.fixture
    async def store(
        self,
    ) -> WorkspaceStore:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    @pytest.fixture
    def session(self) -> str:
        """A session id no earlier test used: a database outlives a test."""
        return f"session-{uuid.uuid4().hex}"

    async def test_a_branch_has_no_head_until_its_first_commit(
        self, store: WorkspaceStore, session: str
    ) -> None:
        assert await store.get_branch_snapshot_head(session, "main") is None
        assert await store.get_snapshot("nope") is None
        assert await store.list_snapshots(session) == []

    async def test_commits_advance_the_head_and_every_snapshot_stays_readable(
        self, store: WorkspaceStore, session: str
    ) -> None:
        first = snapshot(session, "main", None, "one")
        second = snapshot(session, "main", first.id, "two")
        await store.commit_snapshot(
            session, "main", first, expected_parent_snapshot_id=None
        )
        await store.commit_snapshot(
            session, "main", second, expected_parent_snapshot_id=first.id
        )

        assert (await store.get_branch_snapshot_head(session, "main")).id == second.id
        assert (await store.get_snapshot(first.id)).manifest_ref == "one"
        assert [s.id for s in await store.list_snapshots(session)] == [
            first.id,
            second.id,
        ]

    async def test_committing_on_a_stale_parent_is_a_conflict_that_names_both(
        self, store: WorkspaceStore, session: str
    ) -> None:
        first = snapshot(session, "main")
        await store.commit_snapshot(
            session, "main", first, expected_parent_snapshot_id=None
        )

        with pytest.raises(SnapshotConflictError) as raised:
            await store.commit_snapshot(
                session,
                "main",
                snapshot(session, "main", None, "stale"),
                expected_parent_snapshot_id=None,
            )

        assert (
            raised.value.expected_parent_id is None
            and raised.value.actual_parent_id == first.id
        )
        assert (await store.get_branch_snapshot_head(session, "main")).id == first.id, (
            "a refused commit moved the head"
        )

    async def test_a_snapshot_must_agree_with_the_commit_it_is_part_of(
        self, store: WorkspaceStore, session: str
    ) -> None:
        with pytest.raises(ValueError):
            await store.commit_snapshot(
                session,
                "main",
                snapshot("another-session", "main"),
                expected_parent_snapshot_id=None,
            )
        with pytest.raises(ValueError):
            await store.commit_snapshot(
                session,
                "main",
                snapshot(session, "other-branch"),
                expected_parent_snapshot_id=None,
            )
        with pytest.raises(ValueError):
            await store.commit_snapshot(
                session,
                "main",
                snapshot(session, "main", "someone"),
                expected_parent_snapshot_id=None,
            )

    async def test_writers_racing_to_extend_one_head_never_both_win(
        self, store: WorkspaceStore, session: str
    ) -> None:
        base = snapshot(session, "main")
        await store.commit_snapshot(
            session, "main", base, expected_parent_snapshot_id=None
        )

        results = await asyncio.gather(
            *(
                store.commit_snapshot(
                    session,
                    "main",
                    snapshot(session, "main", base.id, f"writer-{i}"),
                    expected_parent_snapshot_id=base.id,
                )
                for i in range(8)
            ),
            return_exceptions=True,
        )

        assert sum(isinstance(r, WorkspaceSnapshot) for r in results) == 1
        assert all(
            isinstance(r, SnapshotConflictError)
            for r in results
            if not isinstance(r, WorkspaceSnapshot)
        )

    async def test_a_forked_branch_starts_at_the_source_head_and_then_diverges(
        self, store: WorkspaceStore, session: str
    ) -> None:
        base = snapshot(session, "main")
        await store.commit_snapshot(
            session, "main", base, expected_parent_snapshot_id=None
        )

        forked = await store.fork_branch_snapshot(session, "main", "feature")
        assert forked is not None and forked.id == base.id
        own = snapshot(session, "feature", base.id, "feature-work")
        await store.commit_snapshot(
            session, "feature", own, expected_parent_snapshot_id=base.id
        )

        assert (await store.get_branch_snapshot_head(session, "feature")).id == own.id
        assert (await store.get_branch_snapshot_head(session, "main")).id == base.id

    async def test_forking_a_branch_with_no_head_gives_nothing_and_a_branch_is_created_once(
        self, store: WorkspaceStore, session: str
    ) -> None:
        assert await store.fork_branch_snapshot(session, "main", "feature") is None
        base = snapshot(session, "main")
        await store.commit_snapshot(
            session, "main", base, expected_parent_snapshot_id=None
        )
        await store.fork_branch_snapshot(session, "main", "feature")
        with pytest.raises(ValueError):
            await store.fork_branch_snapshot(session, "main", "feature")

    async def test_a_head_can_be_pointed_at_an_older_snapshot_once(
        self, store: WorkspaceStore, session: str
    ) -> None:
        first = snapshot(session, "main")
        await store.commit_snapshot(
            session, "main", first, expected_parent_snapshot_id=None
        )
        await store.commit_snapshot(
            session,
            "main",
            snapshot(session, "main", first.id),
            expected_parent_snapshot_id=first.id,
        )

        assert (
            await store.set_branch_snapshot_head(session, "what-if", first.id)
        ).id == first.id
        with pytest.raises(ValueError):
            await store.set_branch_snapshot_head(session, "what-if", first.id)
        with pytest.raises(ValueError):
            await store.set_branch_snapshot_head(
                session, "elsewhere", "no-such-snapshot"
            )

    async def test_snapshots_are_listed_per_session_and_optionally_per_branch(
        self, store: WorkspaceStore, session: str
    ) -> None:
        main, feature = snapshot(session, "main"), snapshot(session, "feature")
        await store.commit_snapshot(
            session, "main", main, expected_parent_snapshot_id=None
        )
        await store.commit_snapshot(
            session, "feature", feature, expected_parent_snapshot_id=None
        )
        other = snapshot(f"{session}-other", "main")
        await store.commit_snapshot(
            f"{session}-other", "main", other, expected_parent_snapshot_id=None
        )

        assert {s.id for s in await store.list_snapshots(session)} == {
            main.id,
            feature.id,
        }
        assert [
            s.id for s in await store.list_snapshots(session, branch_id="feature")
        ] == [feature.id]

    @pytest.mark.parametrize(
        "hostile",
        [
            "x' OR '1'='1",
            "a'; DROP TABLE t; --",
            "../../etc/passwd",
            "a/b\\c",
            "..",
            "ünï-çødé",
        ],
    )
    async def test_a_hostile_session_or_branch_name_is_inert(
        self, store: WorkspaceStore, session: str, hostile: str
    ) -> None:
        bystander = snapshot(session, "main")
        await store.commit_snapshot(
            session, "main", bystander, expected_parent_snapshot_id=None
        )
        mine = snapshot(hostile, hostile)
        await store.commit_snapshot(
            hostile, hostile, mine, expected_parent_snapshot_id=None
        )

        assert (await store.get_branch_snapshot_head(hostile, hostile)).id == mine.id
        assert (
            await store.get_branch_snapshot_head(session, "main")
        ).id == bystander.id
        assert [s.id for s in await store.list_snapshots(hostile)] == [mine.id]


__all__ = ["WorkspaceStoreConformance", "snapshot"]

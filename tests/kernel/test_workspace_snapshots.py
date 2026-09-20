"""Tests for WorkspaceSnapshot, WorkspaceManifest, and InMemoryWorkspaceStore."""

import pytest
from pydantic import ValidationError

from substrate.agents.context.workspace import InMemoryWorkspaceStore
from substrate.kernel.exceptions import SnapshotConflictError
from substrate.kernel.storage.snapshots import (
    ContentRef,
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
)


class TestWorkspaceSnapshotValidation:
    def test_single_manifest_authority_inline(self) -> None:
        manifest = WorkspaceManifest(
            files={
                "main.py": WorkspaceFileEntry(
                    path="main.py",
                    content=ContentRef(hash="sha256-abc", size_bytes=42),
                )
            }
        )
        snap = WorkspaceSnapshot(
            session_id="s1",
            branch_id="main",
            manifest=manifest,
        )
        assert snap.manifest is not None
        assert snap.manifest_ref is None

    def test_single_manifest_authority_ref(self) -> None:
        snap = WorkspaceSnapshot(
            session_id="s1",
            branch_id="main",
            manifest_ref="cas://manifest-hash-123",
        )
        assert snap.manifest is None
        assert snap.manifest_ref == "cas://manifest-hash-123"

    def test_single_manifest_authority_both_rejected(self) -> None:
        manifest = WorkspaceManifest()
        with pytest.raises(ValidationError, match="Exactly one of manifest or manifest_ref"):
            WorkspaceSnapshot(
                session_id="s1",
                branch_id="main",
                manifest=manifest,
                manifest_ref="cas://123",
            )

    def test_single_manifest_authority_neither_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Exactly one of manifest or manifest_ref"):
            WorkspaceSnapshot(
                session_id="s1",
                branch_id="main",
            )


class TestWorkspaceStore:
    @pytest.mark.asyncio
    async def test_initial_commit_and_advance(self) -> None:
        store = InMemoryWorkspaceStore()

        # Branch initially has no head
        assert await store.get_branch_snapshot_head("s1", "main") is None

        # First snapshot with parent None
        snap1 = WorkspaceSnapshot(
            session_id="s1",
            branch_id="main",
            parent_snapshot_id=None,
            manifest_ref="ref-1",
        )
        await store.commit_snapshot("s1", "main", snap1, expected_parent_snapshot_id=None)

        head = await store.get_branch_snapshot_head("s1", "main")
        assert head is not None
        assert head.id == snap1.id

        # Second snapshot advancing head
        snap2 = WorkspaceSnapshot(
            session_id="s1",
            branch_id="main",
            parent_snapshot_id=snap1.id,
            manifest_ref="ref-2",
        )
        await store.commit_snapshot(
            "s1", "main", snap2, expected_parent_snapshot_id=snap1.id
        )

        head2 = await store.get_branch_snapshot_head("s1", "main")
        assert head2 is not None
        assert head2.id == snap2.id

    @pytest.mark.asyncio
    async def test_commit_conflict_raises_snapshot_conflict_error(self) -> None:
        store = InMemoryWorkspaceStore()

        snap1 = WorkspaceSnapshot(
            session_id="s1",
            branch_id="main",
            parent_snapshot_id=None,
            manifest_ref="ref-1",
        )
        await store.commit_snapshot("s1", "main", snap1, expected_parent_snapshot_id=None)

        # Attempt to commit expecting None when current head is snap1.id
        snap_stale = WorkspaceSnapshot(
            session_id="s1",
            branch_id="main",
            parent_snapshot_id=None,
            manifest_ref="ref-stale",
        )
        with pytest.raises(SnapshotConflictError) as exc_info:
            await store.commit_snapshot(
                "s1", "main", snap_stale, expected_parent_snapshot_id=None
            )
        assert exc_info.value.session_id == "s1"
        assert exc_info.value.branch_id == "main"
        assert exc_info.value.expected_parent_id is None
        assert exc_info.value.actual_parent_id == snap1.id

    @pytest.mark.asyncio
    async def test_branch_workspace_isolation(self) -> None:
        store = InMemoryWorkspaceStore()

        # Commit initial snapshot on main
        snap_base = WorkspaceSnapshot(
            session_id="s1",
            branch_id="main",
            parent_snapshot_id=None,
            manifest_ref="base-manifest",
        )
        await store.commit_snapshot("s1", "main", snap_base, expected_parent_snapshot_id=None)

        # Fork branch-a and branch-b from main
        await store.fork_branch_snapshot("s1", "main", "branch-a")
        await store.fork_branch_snapshot("s1", "main", "branch-b")

        assert (await store.get_branch_snapshot_head("s1", "branch-a")).id == snap_base.id
        assert (await store.get_branch_snapshot_head("s1", "branch-b")).id == snap_base.id

        # Branch-a modifies files and commits new snapshot
        snap_a1 = WorkspaceSnapshot(
            session_id="s1",
            branch_id="branch-a",
            parent_snapshot_id=snap_base.id,
            manifest_ref="branch-a-manifest",
        )
        await store.commit_snapshot(
            "s1", "branch-a", snap_a1, expected_parent_snapshot_id=snap_base.id
        )

        # Branch-a is updated
        assert (await store.get_branch_snapshot_head("s1", "branch-a")).id == snap_a1.id

        # Main and branch-b are completely unaffected and isolated!
        assert (await store.get_branch_snapshot_head("s1", "main")).id == snap_base.id
        assert (await store.get_branch_snapshot_head("s1", "branch-b")).id == snap_base.id

        # Branch-b now commits its own changes independently
        snap_b1 = WorkspaceSnapshot(
            session_id="s1",
            branch_id="branch-b",
            parent_snapshot_id=snap_base.id,
            manifest_ref="branch-b-manifest",
        )
        await store.commit_snapshot(
            "s1", "branch-b", snap_b1, expected_parent_snapshot_id=snap_base.id
        )

        assert (await store.get_branch_snapshot_head("s1", "branch-b")).id == snap_b1.id
        assert (await store.get_branch_snapshot_head("s1", "branch-a")).id == snap_a1.id
        assert (await store.get_branch_snapshot_head("s1", "main")).id == snap_base.id

    @pytest.mark.asyncio
    async def test_list_snapshots_filtering(self) -> None:
        store = InMemoryWorkspaceStore()

        snap_m = WorkspaceSnapshot(
            session_id="s1",
            branch_id="main",
            manifest_ref="m",
        )
        await store.commit_snapshot("s1", "main", snap_m, expected_parent_snapshot_id=None)

        snap_f = WorkspaceSnapshot(
            session_id="s1",
            branch_id="feat",
            manifest_ref="f",
        )
        await store.commit_snapshot("s1", "feat", snap_f, expected_parent_snapshot_id=None)

        all_snaps = await store.list_snapshots("s1")
        assert len(all_snaps) == 2

        main_snaps = await store.list_snapshots("s1", branch_id="main")
        assert len(main_snaps) == 1
        assert main_snaps[0].id == snap_m.id

        feat_snaps = await store.list_snapshots("s1", branch_id="feat")
        assert len(feat_snaps) == 1
        assert feat_snaps[0].id == snap_f.id


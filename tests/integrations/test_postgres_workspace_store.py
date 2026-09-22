from __future__ import annotations

import os
import pytest
from sqlalchemy.exc import OperationalError

from substrate.integrations.storage.workspace_store import PostgresWorkspaceStore
from substrate.kernel.exceptions import SnapshotConflictError
from substrate.kernel.storage.snapshots import (
    ContentRef,
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
)

pytestmark = [pytest.mark.requires_postgres]


@pytest.mark.asyncio
async def test_postgres_workspace_store_lifecycle_and_cas():
    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )
    store = PostgresWorkspaceStore(db_url, echo=False)
    try:
        await store.connect()
    except (OperationalError, Exception) as e:
        pytest.skip(f"PostgreSQL database not available: {e}")

    session_id = "test-ws-sess-1"
    try:
        await store.clear_session(session_id)

        # 1. Commit root snapshot on main branch
        snap1 = WorkspaceSnapshot(
            id="snap-1",
            session_id=session_id,
            branch_id="main",
            parent_snapshot_id=None,
            manifest=WorkspaceManifest(
                files={
                    "README.md": WorkspaceFileEntry(
                        path="README.md", content=ContentRef(hash="hash1", size_bytes=100)
                    ),
                }
            ),
        )
        committed1 = await store.commit_snapshot(
            session_id, "main", snap1, expected_parent_snapshot_id=None
        )
        assert committed1.id == "snap-1"

        # 2. Verify head pointer
        head1 = await store.get_branch_snapshot_head(session_id, "main")
        assert head1 is not None
        assert head1.id == "snap-1"
        assert head1.manifest is not None
        assert "README.md" in head1.manifest.files
        assert head1.manifest.files["README.md"].content.hash == "hash1"

        # 3. Conflict on wrong expected parent
        snap2_bad = WorkspaceSnapshot(
            id="snap-2-bad",
            session_id=session_id,
            branch_id="main",
            parent_snapshot_id="wrong-parent",
            manifest_ref="cas://manifests/m2",
        )
        with pytest.raises(ValueError):
            await store.commit_snapshot(
                session_id, "main", snap2_bad, expected_parent_snapshot_id="snap-1"
            )

        snap2_conflict = WorkspaceSnapshot(
            id="snap-2-conflict",
            session_id=session_id,
            branch_id="main",
            parent_snapshot_id=None,  # but active head is snap-1
            manifest_ref="cas://manifests/m2",
        )
        with pytest.raises(SnapshotConflictError):
            await store.commit_snapshot(
                session_id, "main", snap2_conflict, expected_parent_snapshot_id=None
            )

        # 4. Valid advance to snap-2
        snap2 = WorkspaceSnapshot(
            id="snap-2",
            session_id=session_id,
            branch_id="main",
            parent_snapshot_id="snap-1",
            manifest_ref="cas://manifests/m2",
        )
        await store.commit_snapshot(
            session_id, "main", snap2, expected_parent_snapshot_id="snap-1"
        )
        head2 = await store.get_branch_snapshot_head(session_id, "main")
        assert head2 is not None
        assert head2.id == "snap-2"
        assert head2.manifest_ref == "cas://manifests/m2"

        # 5. Fork branch snapshot
        forked = await store.fork_branch_snapshot(session_id, "main", "experiment")
        assert forked is not None
        assert forked.id == "snap-2"

        exp_head = await store.get_branch_snapshot_head(session_id, "experiment")
        assert exp_head is not None
        assert exp_head.id == "snap-2"

        # 6. Branch isolation: advance experiment without affecting main
        snap_exp = WorkspaceSnapshot(
            id="snap-exp-1",
            session_id=session_id,
            branch_id="experiment",
            parent_snapshot_id="snap-2",
            manifest_ref="cas://manifests/m-exp",
        )
        await store.commit_snapshot(
            session_id, "experiment", snap_exp, expected_parent_snapshot_id="snap-2"
        )

        main_head_after = await store.get_branch_snapshot_head(session_id, "main")
        assert main_head_after is not None
        assert main_head_after.id == "snap-2"

        exp_head_after = await store.get_branch_snapshot_head(session_id, "experiment")
        assert exp_head_after is not None
        assert exp_head_after.id == "snap-exp-1"

        # 7. List snapshots
        all_snaps = await store.list_snapshots(session_id)
        assert len(all_snaps) == 3
        main_snaps = await store.list_snapshots(session_id, branch_id="main")
        assert len(main_snaps) == 2
        assert [s.id for s in main_snaps] == ["snap-1", "snap-2"]
    finally:
        await store.clear_session(session_id)
        await store.disconnect()


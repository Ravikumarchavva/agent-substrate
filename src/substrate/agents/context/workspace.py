"""Workspace snapshot store — manages branch-isolated workspace states."""

from __future__ import annotations

from substrate.kernel.exceptions import SnapshotConflictError
from substrate.kernel.storage.snapshots import (
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
    WorkspaceStore,
)


class InMemoryWorkspaceStore(WorkspaceStore):
    """In-memory reference implementation of WorkspaceStore with branch snapshot isolation."""

    def __init__(self) -> None:
        # snapshot_id -> WorkspaceSnapshot
        self._snapshots: dict[str, WorkspaceSnapshot] = {}
        # (session_id, branch_id) -> snapshot_id (active head)
        self._branch_heads: dict[tuple[str, str], str] = {}
        # session_id -> list[WorkspaceSnapshot]
        self._session_snapshots: dict[str, list[WorkspaceSnapshot]] = {}

    async def get_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot | None:
        return self._snapshots.get(snapshot_id)

    async def get_branch_snapshot_head(
        self, session_id: str, branch_id: str
    ) -> WorkspaceSnapshot | None:
        key = (session_id, branch_id)
        head_id = self._branch_heads.get(key)
        if head_id is None:
            return None
        return self._snapshots.get(head_id)

    async def commit_snapshot(
        self,
        session_id: str,
        branch_id: str,
        new_snapshot: WorkspaceSnapshot,
        *,
        expected_parent_snapshot_id: str | None,
    ) -> WorkspaceSnapshot:
        if new_snapshot.session_id != session_id:
            raise ValueError(
                f"Snapshot session '{new_snapshot.session_id}' does not match '{session_id}'"
            )
        if new_snapshot.branch_id != branch_id:
            raise ValueError(
                f"Snapshot branch '{new_snapshot.branch_id}' does not match '{branch_id}'"
            )
        if new_snapshot.parent_snapshot_id != expected_parent_snapshot_id:
            raise ValueError(
                f"Snapshot parent '{new_snapshot.parent_snapshot_id}' does not match expected '{expected_parent_snapshot_id}'"
            )

        key = (session_id, branch_id)
        current_head_id = self._branch_heads.get(key)

        # Optimistic concurrency check
        if current_head_id != expected_parent_snapshot_id:
            raise SnapshotConflictError(
                f"Conflict committing snapshot on branch '{branch_id}': "
                f"expected parent '{expected_parent_snapshot_id}', actual current head is '{current_head_id}'",
                session_id=session_id,
                branch_id=branch_id,
                expected_parent_id=expected_parent_snapshot_id,
                actual_parent_id=current_head_id,
            )

        # Persist snapshot and advance branch head pointer
        self._snapshots[new_snapshot.id] = new_snapshot
        self._branch_heads[key] = new_snapshot.id
        self._session_snapshots.setdefault(session_id, []).append(new_snapshot)
        return new_snapshot

    async def fork_branch_snapshot(
        self,
        session_id: str,
        source_branch_id: str,
        new_branch_id: str,
    ) -> WorkspaceSnapshot | None:
        source_key = (session_id, source_branch_id)
        new_key = (session_id, new_branch_id)
        if new_key in self._branch_heads:
            raise ValueError(
                f"Branch '{new_branch_id}' already has a snapshot pointer in session '{session_id}'"
            )

        head_id = self._branch_heads.get(source_key)
        if head_id is not None:
            self._branch_heads[new_key] = head_id
            return self._snapshots.get(head_id)
        return None

    async def list_snapshots(
        self, session_id: str, branch_id: str | None = None
    ) -> list[WorkspaceSnapshot]:
        snapshots = self._session_snapshots.get(session_id, [])
        if branch_id is not None:
            return [s for s in snapshots if s.branch_id == branch_id]
        return list(snapshots)


from substrate.capabilities.storage.local_workspace_store import (
    LocalFilesystemWorkspaceStore,
)

__all__ = [
    "WorkspaceFileEntry",
    "WorkspaceManifest",
    "WorkspaceSnapshot",
    "WorkspaceStore",
    "InMemoryWorkspaceStore",
    "LocalFilesystemWorkspaceStore",
]


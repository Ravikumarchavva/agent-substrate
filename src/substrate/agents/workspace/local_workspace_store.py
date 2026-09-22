"""LocalFilesystemWorkspaceStore — durable WorkspaceStore backed by JSON files.

Persists branch-isolated workspace snapshots under ``<root>/snapshots/<snapshot_id>.json``
and branch snapshot heads under ``<root>/sessions/<session_id>/heads/<branch_id>.json``.
Guarantees branch state and file manifests persist across restarts without Postgres.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

from substrate.kernel.exceptions import SnapshotConflictError
from substrate.kernel.storage.snapshots import (
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
    WorkspaceStore,
)

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class LocalFilesystemWorkspaceStore(WorkspaceStore):
    """WorkspaceStore backed by local JSON files.

    Parameters
    ----------
    root:
        Root storage directory. Defaults to ``./data/db/workspaces``.
    """

    def __init__(self, root: str | Path = "./data/db/workspaces") -> None:
        self._root = Path(root)
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._lock_map_lock = asyncio.Lock()

    def _snapshot_path(self, snapshot_id: str) -> Path:
        return self._root / "snapshots" / f"{snapshot_id}.json"

    def _head_path(self, session_id: str, branch_id: str) -> Path:
        return self._root / "sessions" / session_id / "heads" / f"{branch_id}.json"

    async def _get_branch_lock(self, session_id: str, branch_id: str) -> asyncio.Lock:
        key = (session_id, branch_id)
        async with self._lock_map_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    async def connect(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "snapshots").mkdir(parents=True, exist_ok=True)
        logger.info("LocalFilesystemWorkspaceStore connected (root=%s)", self._root)

    async def disconnect(self) -> None:
        pass

    def _load_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot | None:
        p = self._snapshot_path(snapshot_id)
        if not p.exists():
            return None
        try:
            return WorkspaceSnapshot.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _load_head_id(self, session_id: str, branch_id: str) -> str | None:
        p = self._head_path(session_id, branch_id)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("snapshot_id")
        except Exception:
            return None

    def _save_head_id(self, session_id: str, branch_id: str, snapshot_id: str) -> None:
        p = self._head_path(session_id, branch_id)
        _atomic_write(p, {"session_id": session_id, "branch_id": branch_id, "snapshot_id": snapshot_id})

    async def get_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot | None:
        return self._load_snapshot(snapshot_id)

    async def get_branch_snapshot_head(
        self, session_id: str, branch_id: str
    ) -> WorkspaceSnapshot | None:
        head_id = self._load_head_id(session_id, branch_id)
        if head_id is None:
            return None
        return self._load_snapshot(head_id)

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

        lock = await self._get_branch_lock(session_id, branch_id)
        async with lock:
            current_head_id = self._load_head_id(session_id, branch_id)
            if current_head_id != expected_parent_snapshot_id:
                raise SnapshotConflictError(
                    f"Conflict committing snapshot on branch '{branch_id}': "
                    f"expected parent '{expected_parent_snapshot_id}', actual current head is '{current_head_id}'",
                    session_id=session_id,
                    branch_id=branch_id,
                    expected_parent_id=expected_parent_snapshot_id,
                    actual_parent_id=current_head_id,
                )

            # Persist snapshot file
            snap_path = self._snapshot_path(new_snapshot.id)
            _atomic_write(snap_path, new_snapshot.model_dump(mode="json"))

            # Advance branch head
            self._save_head_id(session_id, branch_id, new_snapshot.id)
            return new_snapshot

    async def fork_branch_snapshot(
        self,
        session_id: str,
        source_branch_id: str,
        new_branch_id: str,
    ) -> WorkspaceSnapshot | None:
        new_lock = await self._get_branch_lock(session_id, new_branch_id)
        async with new_lock:
            if self._load_head_id(session_id, new_branch_id) is not None:
                raise ValueError(
                    f"Branch '{new_branch_id}' already has a snapshot pointer in session '{session_id}'"
                )

            head_id = self._load_head_id(session_id, source_branch_id)
            if head_id is not None:
                self._save_head_id(session_id, new_branch_id, head_id)
                return self._load_snapshot(head_id)
            return None

    async def set_branch_snapshot_head(
        self, session_id: str, branch_id: str, snapshot_id: str
    ) -> WorkspaceSnapshot:
        lock = await self._get_branch_lock(session_id, branch_id)
        async with lock:
            if self._load_head_id(session_id, branch_id) is not None:
                raise ValueError(
                    f"Branch '{branch_id}' already has a snapshot pointer in session '{session_id}'"
                )
            snapshot = self._load_snapshot(snapshot_id)
            if snapshot is None:
                raise ValueError(f"Snapshot '{snapshot_id}' does not exist")
            self._save_head_id(session_id, branch_id, snapshot_id)
            return snapshot

    async def list_snapshots(
        self, session_id: str, branch_id: str | None = None
    ) -> list[WorkspaceSnapshot]:
        snaps_dir = self._root / "snapshots"
        if not snaps_dir.exists():
            return []
        results = []
        for p in snaps_dir.glob("*.json"):
            try:
                s = WorkspaceSnapshot.model_validate_json(p.read_text(encoding="utf-8"))
                if s.session_id == session_id:
                    if branch_id is None or s.branch_id == branch_id:
                        results.append(s)
            except Exception:
                pass
        results.sort(key=lambda s: s.created_at)
        return results


__all__ = [
    "WorkspaceFileEntry",
    "WorkspaceManifest",
    "WorkspaceSnapshot",
    "WorkspaceStore",
    "LocalFilesystemWorkspaceStore",
]


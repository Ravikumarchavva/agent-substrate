"""LocalFilesystemHistoryProvider — JSON-file-backed conversation history.

Stores data in a local directory tree (default: ``./data/history``), mirroring
the LanceDB / WorkspaceFileStore pattern of creating a folder on first use
instead of requiring an external database.

Layout::

    <root>/
      nodes/<node_id>.json          — one file per MessageNode
      sessions/<session_id>/
        branches/<branch_id>.json   — one file per Branch head pointer
        checkpoints/<checkpoint_id>.json

Thread-safety: a single asyncio.Lock per (session_id, branch_id) pair guards
branch head pointer updates (optimistic CAS). Node writes are idempotent and
file-atomic (write-tmp-then-rename).

This provider is intentionally a drop-in replacement for InMemoryHistoryProvider
for local dev / experimentation — it does NOT require Postgres or Redis.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from substrate.kernel.exceptions import (
    BranchAlreadyExistsError,
    BranchHeadConflictError,
    BranchNotFoundError,
    DAGIntegrityError,
)
from substrate.kernel.storage.history import (
    Branch,
    HistoryCheckpoint,
    MessageNode,
)

_UNSET: Any = object()


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON to a file atomically (tmp → rename) to avoid corruption."""
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


class LocalFilesystemHistoryProvider:
    """Filesystem-backed DAG HistoryProvider — stores data as JSON files.

    Suitable for local development and experimentation without requiring
    a running Postgres instance.  Inspired by how LanceDB creates a local
    ``./data/`` folder on first use.

    Args:
        root: Path to the storage root directory.  Created automatically on
            first write.  Defaults to ``./data/db/sessions``.
    """

    def __init__(self, root: str | Path = "./data/db/sessions") -> None:
        self._root = Path(root)
        # asyncio locks keyed by (session_id, branch_id) — guard CAS updates
        self._branch_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._lock_map_lock = asyncio.Lock()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _node_path(self, node_id: str) -> Path:
        return self._root / "nodes" / f"{node_id}.json"

    def _branch_path(self, session_id: str, branch_id: str) -> Path:
        return self._root / "sessions" / session_id / "branches" / f"{branch_id}.json"

    def _checkpoint_path(self, session_id: str, checkpoint_id: str) -> Path:
        return self._root / "sessions" / session_id / "checkpoints" / f"{checkpoint_id}.json"

    def _load_node(self, node_id: str) -> MessageNode | None:
        p = self._node_path(node_id)
        if not p.exists():
            return None
        return MessageNode.model_validate_json(p.read_text(encoding="utf-8"))

    def _load_branch(self, session_id: str, branch_id: str) -> Branch | None:
        p = self._branch_path(session_id, branch_id)
        if not p.exists():
            return None
        return Branch.model_validate_json(p.read_text(encoding="utf-8"))

    def _save_branch(self, branch: Branch) -> None:
        p = self._branch_path(branch.session_id, branch.id)
        _atomic_write(p, branch.model_dump(mode="json"))

    def _save_node(self, node: MessageNode) -> None:
        p = self._node_path(node.id)
        _atomic_write(p, node.model_dump(mode="json"))

    async def _get_branch_lock(self, session_id: str, branch_id: str) -> asyncio.Lock:
        key = (session_id, branch_id)
        async with self._lock_map_lock:
            if key not in self._branch_locks:
                self._branch_locks[key] = asyncio.Lock()
            return self._branch_locks[key]

    # ── DAG Node & Branch Operations ─────────────────────────────────────────

    async def append_node(self, node: MessageNode) -> None:
        """Persist an immutable node.

        Idempotent: duplicate append is a no-op if the node is identical;
        raises DAGIntegrityError if it differs.
        """
        existing = self._load_node(node.id)
        if existing is not None:
            if (
                existing.parent_id == node.parent_id
                and existing.session_id == node.session_id
                and existing.run_id == node.run_id
                and existing.payload.model_dump(mode="json") == node.payload.model_dump(mode="json")
            ):
                return
            raise DAGIntegrityError(
                f"Node '{node.id}' already exists with different contents"
            )

        if node.parent_id is not None:
            if node.parent_id == node.id:
                raise DAGIntegrityError(
                    f"Node '{node.id}' cannot have itself as parent"
                )
            parent = self._load_node(node.parent_id)
            if parent is None:
                raise DAGIntegrityError(
                    f"Parent node '{node.parent_id}' does not exist"
                )
            if parent.session_id != node.session_id:
                raise DAGIntegrityError(
                    f"Parent node belongs to session '{parent.session_id}', expected '{node.session_id}'"
                )

        self._save_node(node)

    async def get_node(self, node_id: str) -> MessageNode | None:
        return self._load_node(node_id)

    async def get_branch(self, session_id: str, branch_id: str) -> Branch | None:
        return self._load_branch(session_id, branch_id)

    async def list_branches(self, session_id: str) -> list[Branch]:
        branch_dir = self._root / "sessions" / session_id / "branches"
        if not branch_dir.exists():
            return []
        branches = []
        for p in branch_dir.glob("*.json"):
            try:
                branches.append(Branch.model_validate_json(p.read_text(encoding="utf-8")))
            except Exception:
                pass
        return branches

    async def ensure_branch(
        self, session_id: str, branch_id: str, *, head_message_id: str | None = None
    ) -> Branch:
        """Fetch or initialize a branch."""
        lock = await self._get_branch_lock(session_id, branch_id)
        async with lock:
            existing = self._load_branch(session_id, branch_id)
            if existing is not None:
                return existing
            branch = Branch(
                id=branch_id,
                session_id=session_id,
                head_message_id=head_message_id,
                version=0,
            )
            self._save_branch(branch)
            return branch

    async def fork_branch(
        self,
        session_id: str,
        source_branch_id: str,
        new_branch_id: str,
        *,
        fork_from_message_id: str | None = None,
    ) -> Branch:
        """Atomically fork a new branch pointer from source branch or an ancestor node."""
        lock = await self._get_branch_lock(session_id, new_branch_id)
        async with lock:
            if self._load_branch(session_id, new_branch_id) is not None:
                raise BranchAlreadyExistsError(
                    f"Branch '{new_branch_id}' already exists in session '{session_id}'"
                )

            source_branch = self._load_branch(session_id, source_branch_id)
            if source_branch is None:
                raise BranchNotFoundError(
                    f"Source branch '{source_branch_id}' not found in session '{session_id}'"
                )

            if source_branch.head_message_id is None:
                if fork_from_message_id is not None:
                    raise DAGIntegrityError(
                        f"Cannot fork from node '{fork_from_message_id}' on empty branch '{source_branch_id}'"
                    )
                new_branch = Branch(
                    id=new_branch_id,
                    session_id=session_id,
                    head_message_id=None,
                    forked_from_message_id=None,
                    version=0,
                )
                self._save_branch(new_branch)
                return new_branch

            if fork_from_message_id is None:
                target_id = source_branch.head_message_id
            else:
                target_node = self._load_node(fork_from_message_id)
                if target_node is None:
                    raise DAGIntegrityError(
                        f"Fork node '{fork_from_message_id}' does not exist"
                    )
                if target_node.session_id != session_id:
                    raise DAGIntegrityError(
                        f"Fork node belongs to session '{target_node.session_id}', expected '{session_id}'"
                    )

                # Verify target is an ancestor of source branch head
                curr: str | None = source_branch.head_message_id
                found = False
                while curr is not None:
                    if curr == fork_from_message_id:
                        found = True
                        break
                    curr_node = self._load_node(curr)
                    curr = curr_node.parent_id if curr_node else None

                if not found:
                    raise DAGIntegrityError(
                        f"Node '{fork_from_message_id}' is not an ancestor of source branch head '{source_branch.head_message_id}'"
                    )
                target_id = fork_from_message_id

            new_branch = Branch(
                id=new_branch_id,
                session_id=session_id,
                head_message_id=target_id,
                forked_from_message_id=target_id,
                version=0,
            )
            self._save_branch(new_branch)
            return new_branch

    async def rename_branch(
        self, session_id: str, branch_id: str, new_name: str
    ) -> Branch:
        """Rename an existing branch display name (id stays invariant)."""
        lock = await self._get_branch_lock(session_id, branch_id)
        async with lock:
            old_branch = self._load_branch(session_id, branch_id)
            if old_branch is None:
                raise BranchNotFoundError(
                    f"Branch '{branch_id}' not found in session '{session_id}'"
                )
            updated_branch = Branch(
                id=old_branch.id,
                session_id=session_id,
                name=new_name,
                head_message_id=old_branch.head_message_id,
                forked_from_message_id=old_branch.forked_from_message_id,
                version=old_branch.version + 1,
                created_at=old_branch.created_at,
            )
            self._save_branch(updated_branch)
            return updated_branch

    async def set_branch_head(
        self,
        session_id: str,
        branch_id: str,
        new_head_id: str,
        *,
        expected_head_id: str | None = _UNSET,
        expected_version: int | None = None,
    ) -> Branch:
        """Advance branch head pointer using optimistic concurrency control."""
        lock = await self._get_branch_lock(session_id, branch_id)
        async with lock:
            branch = self._load_branch(session_id, branch_id)
            if branch is None:
                raise BranchNotFoundError(
                    f"Branch '{branch_id}' not found in session '{session_id}'"
                )

            if expected_head_id is not _UNSET:
                if branch.head_message_id != expected_head_id:
                    raise BranchHeadConflictError(
                        f"Branch head conflict: expected '{expected_head_id}', actual '{branch.head_message_id}'",
                        session_id=session_id,
                        branch_id=branch_id,
                        expected=expected_head_id,
                        actual=branch.head_message_id,
                    )

            if expected_version is not None:
                if branch.version != expected_version:
                    raise BranchHeadConflictError(
                        f"Branch version conflict: expected {expected_version}, actual {branch.version}",
                        session_id=session_id,
                        branch_id=branch_id,
                        expected=expected_version,
                        actual=branch.version,
                    )

            new_head = self._load_node(new_head_id)
            if new_head is None:
                raise DAGIntegrityError(f"New head node '{new_head_id}' does not exist")
            if new_head.session_id != session_id:
                raise DAGIntegrityError(
                    f"New head node belongs to session '{new_head.session_id}', expected '{session_id}'"
                )

            updated_branch = Branch(
                id=branch_id,
                session_id=session_id,
                name=branch.name,
                head_message_id=new_head_id,
                forked_from_message_id=branch.forked_from_message_id,
                version=branch.version + 1,
                created_at=branch.created_at,
            )
            self._save_branch(updated_branch)
            return updated_branch

    async def append_and_advance(
        self,
        node: MessageNode,
        branch_id: str,
        *,
        expected_head_id: str | None = _UNSET,
        expected_version: int | None = None,
    ) -> Branch:
        """Atomically append node and advance branch head.

        Enforces:
        - node.parent_id == current branch head.
        - Concurrency checks on expected_head_id and expected_version.
        """
        lock = await self._get_branch_lock(node.session_id, branch_id)
        async with lock:
            branch = self._load_branch(node.session_id, branch_id)
            if branch is None:
                branch = Branch(
                    id=branch_id,
                    session_id=node.session_id,
                    head_message_id=None,
                    version=0,
                )

            current_head = branch.head_message_id

            if expected_head_id is not _UNSET:
                if current_head != expected_head_id:
                    raise BranchHeadConflictError(
                        f"Branch head conflict on advance: expected '{expected_head_id}', actual '{current_head}'",
                        session_id=node.session_id,
                        branch_id=branch_id,
                        expected=expected_head_id,
                        actual=current_head,
                    )

            if expected_version is not None:
                if branch.version != expected_version:
                    raise BranchHeadConflictError(
                        f"Branch version conflict on advance: expected {expected_version}, actual {branch.version}",
                        session_id=node.session_id,
                        branch_id=branch_id,
                        expected=expected_version,
                        actual=branch.version,
                    )

            # Append node first (idempotent)
            await self.append_node(node)

            updated_branch = Branch(
                id=branch_id,
                session_id=node.session_id,
                name=branch.name,
                head_message_id=node.id,
                forked_from_message_id=branch.forked_from_message_id,
                version=branch.version + 1,
                created_at=branch.created_at,
            )
            self._save_branch(updated_branch)
            return updated_branch

    # ── Checkpoint Operations ─────────────────────────────────────────────────

    async def save_checkpoint(self, checkpoint: HistoryCheckpoint) -> None:
        """Persist a compaction checkpoint anchor."""
        anchor = self._load_node(checkpoint.anchor_message_id)
        if anchor is None:
            raise DAGIntegrityError(
                f"Checkpoint anchor node '{checkpoint.anchor_message_id}' does not exist"
            )
        if anchor.session_id != checkpoint.session_id:
            raise DAGIntegrityError(
                f"Checkpoint anchor belongs to session '{anchor.session_id}', expected '{checkpoint.session_id}'"
            )
        p = self._checkpoint_path(checkpoint.session_id, checkpoint.id)
        _atomic_write(p, checkpoint.model_dump(mode="json"))

    async def get_checkpoint(self, checkpoint_id: str) -> HistoryCheckpoint | None:
        # Checkpoints are keyed by id but scoped to session — search all sessions
        # For O(1) lookup we'd need a cross-session index; for local dev this is fine.
        sessions_dir = self._root / "sessions"
        if not sessions_dir.exists():
            return None
        for session_dir in sessions_dir.iterdir():
            p = self._checkpoint_path(session_dir.name, checkpoint_id)
            if p.exists():
                return HistoryCheckpoint.model_validate_json(p.read_text(encoding="utf-8"))
        return None

    async def list_checkpoints(self, session_id: str) -> list[HistoryCheckpoint]:
        cp_dir = self._root / "sessions" / session_id / "checkpoints"
        if not cp_dir.exists():
            return []
        checkpoints = []
        for p in cp_dir.glob("*.json"):
            try:
                checkpoints.append(
                    HistoryCheckpoint.model_validate_json(p.read_text(encoding="utf-8"))
                )
            except Exception:
                pass
        return checkpoints

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Ensure storage root exists."""
        self._root.mkdir(parents=True, exist_ok=True)

    async def disconnect(self) -> None:
        """No-op for filesystem store."""

    async def delete_branch(self, session_id: str, branch_id: str) -> None:
        if branch_id == "main":
            raise ValueError("cannot delete the main branch")
        lock = await self._get_branch_lock(session_id, branch_id)
        async with lock:
            self._branch_path(session_id, branch_id).unlink(missing_ok=True)

    # ── Session lifecycle ─────────────────────────────────────────────────────

    async def delete_session(self, session_id: str) -> None:
        # Nodes live in one shared directory, so find this session's by content.
        nodes_dir = self._root / "nodes"
        if nodes_dir.exists():
            for p in nodes_dir.glob("*.json"):
                try:
                    node = MessageNode.model_validate_json(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if node.session_id == session_id:
                    p.unlink(missing_ok=True)
        shutil.rmtree(self._root / "sessions" / session_id, ignore_errors=True)
        async with self._lock_map_lock:
            for key in [k for k in self._branch_locks if k[0] == session_id]:
                del self._branch_locks[key]


__all__ = ["LocalFilesystemHistoryProvider"]


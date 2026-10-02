"""Workspaces — snapshot trees and their branch heads, kept in the store's database.

``Workspaces`` is the one implementation of ``WorkspaceStore`` (``workspace/protocols.py``): ``Workspaces(store)``. A
snapshot is a row; a branch's head is a row naming one. The head only ever moves by compare-and-swap — an ``INSERT`` for
a branch with no head, an ``UPDATE .. WHERE snapshot_id = <expected parent>`` for one that has — so two writers racing
to extend the same head never both win, on any database and with no lock to forget.

It lives in ``workspace`` rather than in ``stores`` because the snapshot types do, and ``stores`` sits below them.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TypeVar

from substrate.stores.database import Row, Tx
from substrate.stores.store import Store
from substrate.types.errors import SnapshotConflictError
from substrate.workspace.protocols import WorkspaceSnapshot

T = TypeVar("T")

SCHEMA = [
    """
CREATE TABLE IF NOT EXISTS workspace_snapshots (
    seq {pk},
    id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS workspace_snapshots_session_idx ON workspace_snapshots (session_id);

CREATE TABLE IF NOT EXISTS workspace_heads (
    session_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    PRIMARY KEY (session_id, branch_id)
);
"""
]


def _snapshot(row: Row | None) -> WorkspaceSnapshot | None:
    return WorkspaceSnapshot.model_validate_json(row["snapshot_json"]) if row else None


async def _head(tx: Tx, session_id: str, branch_id: str) -> str | None:
    row = await tx.fetchone(
        "SELECT snapshot_id FROM workspace_heads WHERE session_id = ? AND branch_id = ?",
        session_id,
        branch_id,
    )
    return row["snapshot_id"] if row else None


async def _read(tx: Tx, snapshot_id: str) -> WorkspaceSnapshot | None:
    return _snapshot(
        await tx.fetchone(
            "SELECT snapshot_json FROM workspace_snapshots WHERE id = ?", snapshot_id
        )
    )


async def _first_head(
    tx: Tx, session_id: str, branch_id: str, snapshot_id: str
) -> None:
    """Give a branch its first head; refuse if it has one — a branch is created once."""
    created = await tx.execute(
        "INSERT INTO workspace_heads (session_id, branch_id, snapshot_id) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
        session_id,
        branch_id,
        snapshot_id,
    )
    if created == 0:
        raise ValueError(
            f"Branch '{branch_id}' already has a snapshot pointer in session '{session_id}'"
        )


class Workspaces:
    """The ``WorkspaceStore`` of a ``Store``."""

    def __init__(self, store: Store) -> None:
        self._store = store

    async def _run(self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        await self._store.ensure("workspace", SCHEMA)
        return await self._store.run(fn)

    async def get_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot | None:
        return await self._run(lambda tx: _read(tx, snapshot_id))

    async def get_branch_snapshot_head(
        self, session_id: str, branch_id: str
    ) -> WorkspaceSnapshot | None:
        async def op(tx: Tx) -> WorkspaceSnapshot | None:
            head = await _head(tx, session_id, branch_id)
            return await _read(tx, head) if head else None

        return await self._run(op)

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

        async def op(tx: Tx) -> WorkspaceSnapshot:
            if expected_parent_snapshot_id is None:
                moved = await tx.execute(
                    "INSERT INTO workspace_heads (session_id, branch_id, snapshot_id) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    session_id,
                    branch_id,
                    new_snapshot.id,
                )
            else:
                moved = await tx.execute(
                    "UPDATE workspace_heads SET snapshot_id = ? WHERE session_id = ? AND branch_id = ? AND snapshot_id = ?",
                    new_snapshot.id,
                    session_id,
                    branch_id,
                    expected_parent_snapshot_id,
                )
            if moved == 0:
                actual = await _head(tx, session_id, branch_id)
                raise SnapshotConflictError(
                    f"Conflict committing snapshot on branch '{branch_id}': "
                    f"expected parent '{expected_parent_snapshot_id}', actual current head is '{actual}'",
                    session_id=session_id,
                    branch_id=branch_id,
                    expected_parent_id=expected_parent_snapshot_id,
                    actual_parent_id=actual,
                )
            await tx.execute(
                "INSERT INTO workspace_snapshots (id, session_id, branch_id, created_at, snapshot_json) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET snapshot_json = excluded.snapshot_json",
                new_snapshot.id,
                session_id,
                branch_id,
                new_snapshot.created_at.isoformat(),
                json.dumps(new_snapshot.model_dump(mode="json")),
            )
            return new_snapshot

        return await self._run(op)

    async def fork_branch_snapshot(
        self, session_id: str, source_branch_id: str, new_branch_id: str
    ) -> WorkspaceSnapshot | None:
        async def op(tx: Tx) -> WorkspaceSnapshot | None:
            if await _head(tx, session_id, new_branch_id) is not None:
                raise ValueError(
                    f"Branch '{new_branch_id}' already has a snapshot pointer in session '{session_id}'"
                )
            head = await _head(tx, session_id, source_branch_id)
            if head is None:
                return None
            await _first_head(tx, session_id, new_branch_id, head)
            return await _read(tx, head)

        return await self._run(op)

    async def set_branch_snapshot_head(
        self, session_id: str, branch_id: str, snapshot_id: str
    ) -> WorkspaceSnapshot:
        async def op(tx: Tx) -> WorkspaceSnapshot:
            if await _head(tx, session_id, branch_id) is not None:
                raise ValueError(
                    f"Branch '{branch_id}' already has a snapshot pointer in session '{session_id}'"
                )
            snapshot = await _read(tx, snapshot_id)
            if snapshot is None:
                raise ValueError(f"Snapshot '{snapshot_id}' does not exist")
            await _first_head(tx, session_id, branch_id, snapshot_id)
            return snapshot

        return await self._run(op)

    async def list_snapshots(
        self, session_id: str, branch_id: str | None = None
    ) -> list[WorkspaceSnapshot]:
        async def op(tx: Tx) -> list[WorkspaceSnapshot]:
            if branch_id is None:
                rows = await tx.fetchall(
                    "SELECT snapshot_json FROM workspace_snapshots WHERE session_id = ? ORDER BY created_at, seq",
                    session_id,
                )
            else:
                rows = await tx.fetchall(
                    "SELECT snapshot_json FROM workspace_snapshots WHERE session_id = ? AND branch_id = ? ORDER BY created_at, seq",
                    session_id,
                    branch_id,
                )
            return [s for s in map(_snapshot, rows) if s is not None]

        return await self._run(op)

    async def delete_session(self, session_id: str) -> None:
        """Remove every snapshot and branch head of ``session_id``."""

        async def op(tx: Tx) -> None:
            await tx.execute(
                "DELETE FROM workspace_heads WHERE session_id = ?", session_id
            )
            await tx.execute(
                "DELETE FROM workspace_snapshots WHERE session_id = ?", session_id
            )

        await self._run(op)


__all__ = ["SCHEMA", "Workspaces"]

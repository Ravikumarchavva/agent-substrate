"""Threads — the conversation DAG, kept in the store's database.

A thread's history is a DAG of immutable nodes; a branch is a movable head pointer into it (``stores/threads.py``).
This is the one implementation of that contract, written once over the store's relational database, so a turn's
messages commit in the same transaction as the run that produced them.

Rules the contract promises and these tables enforce:

* a node is immutable: appending the same node again is a no-op, a different one under the same id is an error;
* a node's parent exists, in the same session — and a node never names itself;
* a branch head moves only by compare-and-swap on the branch's ``version`` (``UPDATE .. WHERE version = ?``), which
  is atomic on any database whether or not it serialises writers, so two writers racing for one head never both win;
* every identifier is a bound parameter, never part of a statement, and never a path — a hostile session or
  branch name is just a string.

Times are ISO-8601 text: exact round trip, and nothing here does arithmetic on them.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

from substrate.stores.database import Row, Tx, under
from substrate.stores.threads import Branch, HistoryCheckpoint, MessageNode
from substrate.types.content import ChatMessage
from substrate.types.errors import (
    BranchAlreadyExistsError,
    BranchHeadConflictError,
    BranchNotFoundError,
    DAGIntegrityError,
)

if TYPE_CHECKING:
    from substrate.stores.store import Store

T = TypeVar("T")

_UNSET: Any = object()

SCHEMA = [
    """
CREATE TABLE IF NOT EXISTS thread_nodes (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    parent_id TEXT,
    run_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    workspace_snapshot_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS thread_nodes_session_idx ON thread_nodes (session_id);

CREATE TABLE IF NOT EXISTS thread_branches (
    session_id TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT,
    head_message_id TEXT,
    forked_from_message_id TEXT,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, id)
);

CREATE TABLE IF NOT EXISTS thread_checkpoints (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    anchor_message_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    state_json TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS thread_checkpoints_session_idx ON thread_checkpoints (session_id);
"""
]

_NODE = (
    "id, session_id, parent_id, run_id, payload_json, workspace_snapshot_id, created_at"
)
_BRANCH = (
    "session_id, id, name, head_message_id, forked_from_message_id, version, created_at"
)
_CHECKPOINT = "id, session_id, anchor_message_id, summary, state_json, parent_checkpoint_id, created_at"


def _node(row: Row) -> MessageNode:
    return MessageNode(
        id=row["id"],
        session_id=row["session_id"],
        parent_id=row["parent_id"],
        run_id=row["run_id"],
        payload=ChatMessage.model_validate(json.loads(row["payload_json"])),
        workspace_snapshot_id=row["workspace_snapshot_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _branch(row: Row) -> Branch:
    return Branch(
        id=row["id"],
        session_id=row["session_id"],
        name=row["name"],
        head_message_id=row["head_message_id"],
        forked_from_message_id=row["forked_from_message_id"],
        version=row["version"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _checkpoint(row: Row) -> HistoryCheckpoint:
    return HistoryCheckpoint(
        id=row["id"],
        session_id=row["session_id"],
        anchor_message_id=row["anchor_message_id"],
        summary=row["summary"],
        state=json.loads(row["state_json"]),
        parent_checkpoint_id=row["parent_checkpoint_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _payload(node: MessageNode) -> str:
    return json.dumps(node.payload.model_dump(mode="json"))


async def _read_node(tx: Tx, node_id: str) -> MessageNode | None:
    row = await tx.fetchone(f"SELECT {_NODE} FROM thread_nodes WHERE id = ?", node_id)
    return _node(row) if row else None


async def _read_branch(tx: Tx, session_id: str, branch_id: str) -> Branch | None:
    row = await tx.fetchone(
        f"SELECT {_BRANCH} FROM thread_branches WHERE session_id = ? AND id = ?",
        session_id,
        branch_id,
    )
    return _branch(row) if row else None


async def _insert_branch(tx: Tx, branch: Branch) -> bool:
    """Insert ``branch``; False if one is already there."""
    inserted = await tx.execute(
        f"INSERT INTO thread_branches ({_BRANCH}) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        branch.session_id,
        branch.id,
        branch.name,
        branch.head_message_id,
        branch.forked_from_message_id,
        branch.version,
        branch.created_at.isoformat(),
    )
    return inserted == 1


async def _append_node(tx: Tx, node: MessageNode) -> None:
    existing = await _read_node(tx, node.id)
    if existing is not None:
        if (
            existing.parent_id == node.parent_id
            and existing.session_id == node.session_id
            and existing.run_id == node.run_id
            and existing.payload.model_dump(mode="json")
            == node.payload.model_dump(mode="json")
        ):
            return
        raise DAGIntegrityError(
            f"Node '{node.id}' already exists with different contents"
        )

    if node.parent_id is not None:
        if node.parent_id == node.id:
            raise DAGIntegrityError(f"Node '{node.id}' cannot have itself as parent")
        parent = await _read_node(tx, node.parent_id)
        if parent is None:
            raise DAGIntegrityError(f"Parent node '{node.parent_id}' does not exist")
        if parent.session_id != node.session_id:
            raise DAGIntegrityError(
                f"Parent node belongs to session '{parent.session_id}', expected '{node.session_id}'"
            )

    inserted = await tx.execute(
        f"INSERT INTO thread_nodes ({_NODE}) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        node.id,
        node.session_id,
        node.parent_id,
        node.run_id,
        _payload(node),
        node.workspace_snapshot_id,
        node.created_at.isoformat(),
    )
    if inserted == 0:  # another writer appended this id between the read and the insert
        await _append_node(tx, node)


def _conflict(
    message: str, session_id: str, branch_id: str, expected: Any, actual: Any
) -> BranchHeadConflictError:
    return BranchHeadConflictError(
        message,
        session_id=session_id,
        branch_id=branch_id,
        expected=expected,
        actual=actual,
    )


def _check_expectations(
    branch: Branch,
    session_id: str,
    branch_id: str,
    expected_head_id: Any,
    expected_version: int | None,
    *,
    on_advance: bool,
) -> None:
    suffix = " on advance" if on_advance else ""
    if expected_head_id is not _UNSET and branch.head_message_id != expected_head_id:
        raise _conflict(
            f"Branch head conflict{suffix}: expected '{expected_head_id}', actual '{branch.head_message_id}'",
            session_id,
            branch_id,
            expected_head_id,
            branch.head_message_id,
        )
    if expected_version is not None and branch.version != expected_version:
        raise _conflict(
            f"Branch version conflict{suffix}: expected {expected_version}, actual {branch.version}",
            session_id,
            branch_id,
            expected_version,
            branch.version,
        )


async def _advance(
    tx: Tx, branch: Branch, head_id: str, *, name: str | None = None
) -> Branch:
    """Move ``branch`` to ``head_id`` if nobody else moved it since it was read — the compare-and-swap."""
    moved = await tx.execute(
        "UPDATE thread_branches SET head_message_id = ?, name = ?, version = version + 1 "
        "WHERE session_id = ? AND id = ? AND version = ?",
        head_id,
        branch.name if name is None else name,
        branch.session_id,
        branch.id,
        branch.version,
    )
    if moved == 0:
        raise _conflict(
            f"Branch '{branch.id}' was changed concurrently",
            branch.session_id,
            branch.id,
            branch.version,
            "changed",
        )
    updated = await _read_branch(tx, branch.session_id, branch.id)
    assert updated is not None
    return updated


class Threads:
    """The ``ThreadStore`` of a ``Store``: ``store.threads``."""

    def __init__(self, store: Store) -> None:
        self._store = store

    @property
    def store(self) -> Store:
        """The store these threads live in — for a host that opened it and has to close it."""
        return self._store

    async def _run(self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        return await self._store.run(fn)

    # ── DAG nodes and branches ────────────────────────────────────────────────

    async def append_node(self, node: MessageNode) -> None:
        async def op(tx: Tx) -> None:
            await _append_node(tx, node)

        await self._run(op)

    async def get_node(self, node_id: str) -> MessageNode | None:
        return await self._run(lambda tx: _read_node(tx, node_id))

    async def get_branch(self, session_id: str, branch_id: str) -> Branch | None:
        return await self._run(lambda tx: _read_branch(tx, session_id, branch_id))

    async def list_branches(self, session_id: str) -> list[Branch]:
        async def op(tx: Tx) -> list[Branch]:
            rows = await tx.fetchall(
                f"SELECT {_BRANCH} FROM thread_branches WHERE session_id = ? ORDER BY created_at, id",
                session_id,
            )
            return [_branch(row) for row in rows]

        return await self._run(op)

    async def ensure_branch(
        self, session_id: str, branch_id: str, *, head_message_id: str | None = None
    ) -> Branch:
        async def op(tx: Tx) -> Branch:
            await _insert_branch(
                tx,
                Branch(
                    id=branch_id,
                    session_id=session_id,
                    head_message_id=head_message_id,
                    version=0,
                ),
            )
            branch = await _read_branch(tx, session_id, branch_id)
            assert branch is not None
            return branch

        return await self._run(op)

    async def fork_branch(
        self,
        session_id: str,
        source_branch_id: str,
        new_branch_id: str,
        *,
        fork_from_message_id: str | None = None,
    ) -> Branch:
        async def op(tx: Tx) -> Branch:
            if await _read_branch(tx, session_id, new_branch_id) is not None:
                raise BranchAlreadyExistsError(
                    f"Branch '{new_branch_id}' already exists in session '{session_id}'"
                )
            source = await _read_branch(tx, session_id, source_branch_id)
            if source is None:
                raise BranchNotFoundError(
                    f"Source branch '{source_branch_id}' not found in session '{session_id}'"
                )

            target_id: str | None
            if source.head_message_id is None:
                if fork_from_message_id is not None:
                    raise DAGIntegrityError(
                        f"Cannot fork from node '{fork_from_message_id}' on empty branch '{source_branch_id}'"
                    )
                target_id = None
            elif fork_from_message_id is None:
                target_id = source.head_message_id
            else:
                target = await _read_node(tx, fork_from_message_id)
                if target is None:
                    raise DAGIntegrityError(
                        f"Fork node '{fork_from_message_id}' does not exist"
                    )
                if target.session_id != session_id:
                    raise DAGIntegrityError(
                        f"Fork node belongs to session '{target.session_id}', expected '{session_id}'"
                    )
                ancestor = await tx.fetchone(
                    "WITH RECURSIVE ancestry(id, parent_id) AS ("
                    " SELECT id, parent_id FROM thread_nodes WHERE id = ?"
                    " UNION ALL"
                    " SELECT n.id, n.parent_id FROM thread_nodes n JOIN ancestry a ON n.id = a.parent_id"
                    ") SELECT id FROM ancestry WHERE id = ? LIMIT 1",
                    source.head_message_id,
                    fork_from_message_id,
                )
                if ancestor is None:
                    raise DAGIntegrityError(
                        f"Node '{fork_from_message_id}' is not an ancestor of source branch head '{source.head_message_id}'"
                    )
                target_id = fork_from_message_id

            forked = Branch(
                id=new_branch_id,
                session_id=session_id,
                head_message_id=target_id,
                forked_from_message_id=target_id,
                version=0,
            )
            if not await _insert_branch(tx, forked):
                raise BranchAlreadyExistsError(
                    f"Branch '{new_branch_id}' already exists in session '{session_id}'"
                )
            return forked

        return await self._run(op)

    async def rename_branch(
        self, session_id: str, branch_id: str, new_name: str
    ) -> Branch:
        async def op(tx: Tx) -> Branch:
            branch = await _read_branch(tx, session_id, branch_id)
            if branch is None:
                raise BranchNotFoundError(
                    f"Branch '{branch_id}' not found in session '{session_id}'"
                )
            renamed = await tx.execute(
                "UPDATE thread_branches SET name = ?, version = version + 1 "
                "WHERE session_id = ? AND id = ? AND version = ?",
                new_name,
                session_id,
                branch_id,
                branch.version,
            )
            if renamed == 0:
                raise _conflict(
                    f"Branch '{branch_id}' was changed concurrently",
                    session_id,
                    branch_id,
                    branch.version,
                    "changed",
                )
            updated = await _read_branch(tx, session_id, branch_id)
            assert updated is not None
            return updated

        return await self._run(op)

    async def set_branch_head(
        self,
        session_id: str,
        branch_id: str,
        new_head_id: str,
        *,
        expected_head_id: str | None = _UNSET,
        expected_version: int | None = None,
    ) -> Branch:
        async def op(tx: Tx) -> Branch:
            branch = await _read_branch(tx, session_id, branch_id)
            if branch is None:
                raise BranchNotFoundError(
                    f"Branch '{branch_id}' not found in session '{session_id}'"
                )
            _check_expectations(
                branch,
                session_id,
                branch_id,
                expected_head_id,
                expected_version,
                on_advance=False,
            )
            head = await _read_node(tx, new_head_id)
            if head is None:
                raise DAGIntegrityError(f"New head node '{new_head_id}' does not exist")
            if head.session_id != session_id:
                raise DAGIntegrityError(
                    f"New head node belongs to session '{head.session_id}', expected '{session_id}'"
                )
            return await _advance(tx, branch, new_head_id)

        return await self._run(op)

    async def append_and_advance(
        self,
        node: MessageNode,
        branch_id: str,
        *,
        expected_head_id: str | None = _UNSET,
        expected_version: int | None = None,
    ) -> Branch:
        async def op(tx: Tx) -> Branch:
            existing = await _read_branch(tx, node.session_id, branch_id)
            branch = existing or Branch(
                id=branch_id,
                session_id=node.session_id,
                head_message_id=None,
                version=0,
            )
            # The new node must extend the head: advancing past a node that is not its parent would
            # silently orphan whatever the head pointed at.
            if node.parent_id != branch.head_message_id:
                raise _conflict(
                    f"Cannot advance branch '{branch_id}': node parent '{node.parent_id}' does not match "
                    f"current head '{branch.head_message_id}'",
                    node.session_id,
                    branch_id,
                    branch.head_message_id,
                    node.parent_id,
                )
            _check_expectations(
                branch,
                node.session_id,
                branch_id,
                expected_head_id,
                expected_version,
                on_advance=True,
            )
            await _append_node(tx, node)
            if existing is not None:
                return await _advance(tx, branch, node.id)
            created = branch.model_copy(
                update={"head_message_id": node.id, "version": 1}
            )
            if not await _insert_branch(tx, created):
                raise _conflict(
                    f"Branch '{branch_id}' was created concurrently",
                    node.session_id,
                    branch_id,
                    None,
                    "created",
                )
            return created

        return await self._run(op)

    # ── Checkpoints ───────────────────────────────────────────────────────────

    async def save_checkpoint(self, checkpoint: HistoryCheckpoint) -> None:
        async def op(tx: Tx) -> None:
            anchor = await _read_node(tx, checkpoint.anchor_message_id)
            if anchor is None:
                raise DAGIntegrityError(
                    f"Checkpoint anchor node '{checkpoint.anchor_message_id}' does not exist"
                )
            if anchor.session_id != checkpoint.session_id:
                raise DAGIntegrityError(
                    f"Checkpoint anchor belongs to session '{anchor.session_id}', expected '{checkpoint.session_id}'"
                )
            await tx.execute(
                f"INSERT INTO thread_checkpoints ({_CHECKPOINT}) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET session_id = excluded.session_id, "
                "anchor_message_id = excluded.anchor_message_id, summary = excluded.summary, "
                "state_json = excluded.state_json, parent_checkpoint_id = excluded.parent_checkpoint_id, "
                "created_at = excluded.created_at",
                checkpoint.id,
                checkpoint.session_id,
                checkpoint.anchor_message_id,
                checkpoint.summary,
                json.dumps(checkpoint.state),
                checkpoint.parent_checkpoint_id,
                checkpoint.created_at.isoformat(),
            )

        await self._run(op)

    async def get_checkpoint(self, checkpoint_id: str) -> HistoryCheckpoint | None:
        async def op(tx: Tx) -> HistoryCheckpoint | None:
            row = await tx.fetchone(
                f"SELECT {_CHECKPOINT} FROM thread_checkpoints WHERE id = ?",
                checkpoint_id,
            )
            return _checkpoint(row) if row else None

        return await self._run(op)

    async def list_checkpoints(self, session_id: str) -> list[HistoryCheckpoint]:
        async def op(tx: Tx) -> list[HistoryCheckpoint]:
            rows = await tx.fetchall(
                f"SELECT {_CHECKPOINT} FROM thread_checkpoints WHERE session_id = ? ORDER BY created_at, id",
                session_id,
            )
            return [_checkpoint(row) for row in rows]

        return await self._run(op)

    # ── Deletion ──────────────────────────────────────────────────────────────

    async def delete_branch(self, session_id: str, branch_id: str) -> None:
        if branch_id == "main":
            raise ValueError("cannot delete the main branch")

        async def op(tx: Tx) -> None:
            await tx.execute(
                "DELETE FROM thread_branches WHERE session_id = ? AND id = ?",
                session_id,
                branch_id,
            )

        await self._run(op)

    async def delete_session(self, session_id: str) -> None:
        async def op(tx: Tx) -> None:
            await tx.execute(
                "DELETE FROM thread_checkpoints WHERE session_id = ?", session_id
            )
            await tx.execute(
                "DELETE FROM thread_branches WHERE session_id = ?", session_id
            )
            await tx.execute(
                "DELETE FROM thread_nodes WHERE session_id = ?", session_id
            )

        await self._run(op)

    async def erase_under(self, name: str) -> int:
        """Delete every session named ``name`` or below it, with its branches and checkpoints. Returns the nodes removed."""
        clause, params = under("session_id", name)

        async def op(tx: Tx) -> int:
            await tx.execute(f"DELETE FROM thread_checkpoints WHERE {clause}", *params)
            await tx.execute(f"DELETE FROM thread_branches WHERE {clause}", *params)
            return await tx.execute(f"DELETE FROM thread_nodes WHERE {clause}", *params)

        erased = await self._run(op)
        if erased:
            await self._store.database.reclaim()
        return erased


__all__ = ["SCHEMA", "Threads"]

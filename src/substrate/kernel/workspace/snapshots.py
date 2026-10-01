"""Commit and checkout orchestration over the kernel ``WorkspaceStore``.

Wires ``materialize.py``'s pure directory<->manifest functions to the
durable branch-head bookkeeping (``WorkspaceStore.get_branch_snapshot_head``/
``commit_snapshot``, with its built-in CAS on ``expected_parent_snapshot_id``).
"""

from __future__ import annotations

from pathlib import Path

from substrate.kernel.storage.snapshots import WorkspaceSnapshot, WorkspaceStore

from .cas import BlobCAS
from .materialize import commit as _commit_dir
from .materialize import materialize as _materialize_manifest


async def checkout_branch(
    store: WorkspaceStore,
    cas: BlobCAS,
    dest: Path,
    *,
    session_id: str,
    branch_id: str,
) -> WorkspaceSnapshot | None:
    """Materialize *branch_id*'s current head into *dest*.

    Returns the head snapshot materialized, or ``None`` if the branch has no
    snapshot yet (a brand-new branch/conversation — *dest* is left empty,
    not an error).
    """
    head = await store.get_branch_snapshot_head(session_id, branch_id)
    if head is None:
        dest.mkdir(parents=True, exist_ok=True)
        return None
    if head.manifest is None:
        raise NotImplementedError(
            "checkout_branch: snapshot uses manifest_ref (external CAS pointer), "
            "which no writer in this codebase produces yet — only inline "
            "manifests are supported"
        )
    await _materialize_manifest(cas, head.manifest, dest)
    return head


_UNSET = object()


async def commit_turn(
    store: WorkspaceStore,
    cas: BlobCAS,
    root: Path,
    *,
    session_id: str,
    branch_id: str,
    expected_parent_id: str | None | object = _UNSET,
) -> WorkspaceSnapshot:
    """Hash *root*'s current contents and advance *branch_id*'s head.

    ``expected_parent_id`` should be the snapshot id the caller actually
    materialized *root* from (e.g. what ``checkout_branch`` returned) —
    passing it explicitly is what makes the CAS check real: a checkout,
    then a run, then a commit is three separate calls, so if
    ``get_branch_snapshot_head`` were instead re-read fresh right here,
    a concurrent writer's commit landing in between would go undetected —
    this call would silently use *that* commit as its parent instead of the
    one the caller actually started from, defeating the whole point of a
    CAS check. Left unset (the default), this falls back to reading the
    head fresh — correct only for a caller that didn't checkout first (a
    one-shot commit with no prior read, or a test).

    Two concurrent commits genuinely racing the same branch — same
    ``expected_parent_id``, both compare-and-swap against it — still fail
    one of them with ``SnapshotConflictError`` rather than silently losing
    a change; callers that need to retry should re-checkout the new head
    and re-diff, not blindly retry with the same directory (out of scope
    for v1, which has no merge story; see the workspace plan's explicit
    non-goals).
    """
    if expected_parent_id is _UNSET:
        parent = await store.get_branch_snapshot_head(session_id, branch_id)
        parent_id = parent.id if parent is not None else None
    else:
        parent_id = expected_parent_id  # type: ignore[assignment]
        parent = await store.get_snapshot(parent_id) if parent_id is not None else None

    new_snapshot = await _commit_dir(
        cas, root, session_id=session_id, branch_id=branch_id, parent=parent
    )
    return await store.commit_snapshot(
        session_id,
        branch_id,
        new_snapshot,
        expected_parent_snapshot_id=parent_id,
    )


__all__ = ["checkout_branch", "commit_turn"]

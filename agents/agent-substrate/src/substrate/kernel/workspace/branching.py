"""Branch lifecycle for a conversation's workspace: fork and delete.

Fork is the headline fix this package exists for: forking a branch's
workspace is a ``WorkspaceStore.fork_branch_snapshot`` call — O(1), a new
branch-head pointer at the same existing snapshot, zero bytes copied, no
quota charged — not the old ``copy_prefix`` byte-copy that
``serving/monolith/routes/branches.py`` used to do (and that dead-ended:
see the workspace plan's Context section for why the old mechanism never
actually took effect).

There is no rename here: renaming a branch changes ``Branch.name`` (a
display label) in ``HistoryProvider``, never ``Branch.id`` — the workspace
snapshot chain is keyed by the immutable branch id and needs no touching on
a rename. (The two ``rename_branch_snapshot`` methods that used to exist on
``WorkspaceStore`` implementations were dead code for exactly this reason —
removed, not fixed.)
"""

from __future__ import annotations

from substrate.kernel.storage.history import HistoryProvider
from substrate.kernel.storage.objects import ObjectStore
from substrate.kernel.storage.snapshots import WorkspaceSnapshot, WorkspaceStore

from .layout import conversation_workspace_prefix


async def resolve_workspace_snapshot_id(
    history: HistoryProvider, node_id: str | None
) -> str | None:
    """Walk a DAG node's ancestry to find the nearest turn-boundary snapshot.

    ``MessageNode.workspace_snapshot_id`` is only set on the last node of a
    turn (see ``agents/core/_loop.py::persist_turns``), so forking from an
    arbitrary node — most of which don't carry a snapshot id directly —
    needs to walk up ``parent_id`` until it finds one. Returns ``None`` if
    the chain has no snapshot anywhere (no turn in this ancestry ever
    committed a workspace, or workspace commits haven't been wired in yet).
    """
    while node_id is not None:
        node = await history.get_node(node_id)
        if node is None:
            return None
        if node.workspace_snapshot_id is not None:
            return node.workspace_snapshot_id
        node_id = node.parent_id
    return None


async def fork_branch(
    store: WorkspaceStore,
    *,
    session_id: str,
    source_branch_id: str,
    new_branch_id: str,
    from_snapshot_id: str | None = None,
) -> WorkspaceSnapshot | None:
    """Point *new_branch_id* at a workspace snapshot — O(1), zero bytes copied.

    Two cases:
    - ``from_snapshot_id`` given (a fork from a specific historical DAG
      node — see ``resolve_workspace_snapshot_id``): point the new branch
      directly at that snapshot via ``set_branch_snapshot_head``.
    - Otherwise: fork from *source_branch_id*'s current head via
      ``fork_branch_snapshot`` — the common "duplicate this conversation as
      of now" case.

    Returns the shared snapshot, or ``None`` if there's nothing to fork yet
    (the source has no snapshot — the new branch also starts empty).
    """
    if from_snapshot_id is not None:
        snapshot = await store.get_snapshot(from_snapshot_id)
        if snapshot is None:
            return None
        return await store.set_branch_snapshot_head(
            session_id, new_branch_id, from_snapshot_id
        )
    return await store.fork_branch_snapshot(session_id, source_branch_id, new_branch_id)


async def delete_branch_workspace(
    object_store: ObjectStore,
    *,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    branch_id: str,
) -> int:
    """Delete a branch's workspace object-storage prefix.

    The delete-on-delete half of the v1 garbage-collection posture (see the
    workspace plan): a branch's *blobs* are never individually swept — they
    live under the user's shared CAS prefix and are reclaimed only when the
    whole user/conversation is erased — but a deleted branch's *materialized
    workspace prefix* (if anything ever wrote loose files there outside the
    snapshot/CAS path) is removed immediately. Returns the object count
    removed.
    """
    prefix = conversation_workspace_prefix(tenant_id, user_id, conversation_id, branch_id)
    return await object_store.delete_prefix(prefix)


__all__ = ["fork_branch", "delete_branch_workspace", "resolve_workspace_snapshot_id"]

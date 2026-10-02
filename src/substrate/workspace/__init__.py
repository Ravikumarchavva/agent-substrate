"""substrate.workspace — Per-thread workspaces: branching, snapshots, content-addressed files."""

from __future__ import annotations

from substrate.workspace.branching import (
    delete_branch_workspace,
    fork_branch,
    resolve_workspace_snapshot_id,
)
from substrate.workspace.cas import (
    BlobCAS,
)
from substrate.workspace.tables import (
    Workspaces,
)
from substrate.workspace.protocols import (
    ContentRef,
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
    WorkspaceStore,
)
from substrate.workspace.scope import (
    WorkspaceScope,
    workspace_scope,
)
from substrate.workspace.snapshots import (
    checkout_branch,
    commit_turn,
)

__all__ = [
    "BlobCAS",
    "ContentRef",
    "Workspaces",
    "WorkspaceFileEntry",
    "WorkspaceManifest",
    "WorkspaceScope",
    "WorkspaceSnapshot",
    "WorkspaceStore",
    "checkout_branch",
    "commit_turn",
    "delete_branch_workspace",
    "fork_branch",
    "resolve_workspace_snapshot_id",
    "workspace_scope",
]

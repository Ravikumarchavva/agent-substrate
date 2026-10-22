"""loamz.engine.agents.stores.workspace — Per-thread workspaces: branching, snapshots, content-addressed files."""

from __future__ import annotations

from loamz.engine.agents.kernel.ports.workspace import (
    ContentRef,
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
    WorkspaceStore,
)
from loamz.engine.agents.stores.workspace.branching import (
    delete_branch_workspace,
    fork_branch,
    resolve_workspace_snapshot_id,
)
from loamz.engine.agents.stores.workspace.cas import BlobCAS
from loamz.engine.agents.stores.workspace.scope import WorkspaceScope, workspace_scope
from loamz.engine.agents.stores.workspace.snapshots import checkout_branch, commit_turn
from loamz.engine.agents.stores.workspace.tables import Workspaces

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

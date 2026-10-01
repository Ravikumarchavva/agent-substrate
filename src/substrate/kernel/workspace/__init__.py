"""agents.workspace — where a user's files live, and who may see them.

The one place that owns object-storage key layout (``layout.py``), workspace
identity (``scope.py``), content addressing (``cas.py``), directory<->manifest
materialize/commit (``materialize.py``), branch-head commit orchestration
(``snapshots.py``), and branch lifecycle (``branching.py``).

Every workspace-touching call site elsewhere in the codebase should build
keys through ``layout.py`` and scope through ``scope.WorkspaceScope`` —
nothing outside this package constructs a workspace object-storage key.
"""

from __future__ import annotations

from substrate.kernel.workspace.branching import (
    delete_branch_workspace,
    fork_branch,
    resolve_workspace_snapshot_id,
)
from substrate.kernel.workspace.cas import BlobCAS
from substrate.kernel.workspace.local_workspace_store import (
    LocalFilesystemWorkspaceStore,
)
from substrate.kernel.workspace.scope import (
    WorkspaceScope,
    workspace_scope,
)
from substrate.kernel.workspace.snapshots import checkout_branch, commit_turn

__all__ = [
    "WorkspaceScope",
    "workspace_scope",
    "BlobCAS",
    "checkout_branch",
    "commit_turn",
    "fork_branch",
    "delete_branch_workspace",
    "resolve_workspace_snapshot_id",
    "LocalFilesystemWorkspaceStore",
]

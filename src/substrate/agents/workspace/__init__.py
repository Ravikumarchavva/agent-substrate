"""agents.workspace — where a user's files live, and who may see them.

The one place that owns object-storage key layout (``layout.py``), workspace
identity (``scope.py``), content addressing (``cas.py``), snapshot
commit/diff (``snapshots.py``), branch lifecycle (``branching.py``), and
materializing a branch into a sandbox directory (``materialize.py``).

Every workspace-touching call site elsewhere in the codebase should build
keys through ``layout.py`` and scope through ``scope.WorkspaceScope`` —
nothing outside this package constructs a workspace object-storage key.
"""

from __future__ import annotations

from substrate.agents.workspace.scope import (
    WorkspaceScope,
    current_branch_id,
    current_scope,
    current_tenant_id,
    current_user_id,
)

__all__ = [
    "WorkspaceScope",
    "current_user_id",
    "current_tenant_id",
    "current_branch_id",
    "current_scope",
]

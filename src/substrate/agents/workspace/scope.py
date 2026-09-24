"""WorkspaceScope — identifies exactly one branch's workspace, unambiguously.

Built from the run's ``RunScope`` (``ctx.scope``), which the agent sets as it
starts handling each inbound message; nothing here reads ambient state.
"""

from __future__ import annotations

from substrate.kernel.agent.runtime_context import RunScope
from substrate.kernel.core.content import KernelModel


class WorkspaceScope(KernelModel):
    """Identifies exactly one branch's workspace, unambiguously.

    The single object that should be threaded through workspace code instead
    of four loose strings — ``agents/workspace/layout.py``'s key builders,
    the CAS, and the code interpreter all take a ``WorkspaceScope``.
    """

    tenant_id: str
    user_id: str
    conversation_id: str
    branch_id: str = "main"


def workspace_scope(scope: RunScope, conversation_id: str) -> WorkspaceScope | None:
    """The workspace *scope* owns for *conversation_id*.

    ``None`` when tenant or user isn't known — the same "unauthenticated /
    non-conversation context" case every workspace-touching call site must
    already handle explicitly rather than defaulting silently.
    """
    if scope.tenant_id is None or scope.user_id is None:
        return None
    return WorkspaceScope(
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        conversation_id=conversation_id,
        branch_id=scope.branch_id,
    )


__all__ = ["WorkspaceScope", "workspace_scope"]

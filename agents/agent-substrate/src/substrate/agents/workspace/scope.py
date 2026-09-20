"""WorkspaceScope and the ambient ContextVars identifying whose workspace this is.

``current_user_id``/``current_tenant_id`` moved down from
``agents/storage/tasks.py`` — they identify workspace ownership, not task-list
identity, and belong beside the rest of the workspace package.
``current_branch_id`` is new: nothing tracked which branch a running agent's
files belong to before this, which is exactly why the code interpreter could
never be branch-aware (see ``code_interpreter/tool.py``, which always
resolved to the conversation's ``main`` workspace regardless of which branch
was actually active).

Set once per inbound message, inside the Worker task that will run the agent
(same cross-task-boundary reasoning as ``agents/storage/tasks.py``'s
ContextVars: a value set in the SSE generator does not cross into the Worker's
own asyncio context, so these must be stamped from inside
``ReActAgent._handle_message`` / ``OrchestratorAgent`` itself, not upstream).
"""

from __future__ import annotations

import contextvars

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


current_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "workspace_user_id", default=None
)
current_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "workspace_tenant_id", default=None
)
current_branch_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "workspace_branch_id", default="main"
)


def current_scope(conversation_id: str) -> WorkspaceScope | None:
    """Build a ``WorkspaceScope`` from the ambient ContextVars.

    ``None`` when tenant or user isn't set — the same "unauthenticated /
    non-conversation context" case every workspace-touching call site must
    already handle explicitly rather than defaulting silently.
    """
    tenant_id = current_tenant_id.get()
    user_id = current_user_id.get()
    if tenant_id is None or user_id is None:
        return None
    return WorkspaceScope(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        branch_id=current_branch_id.get(),
    )


__all__ = [
    "WorkspaceScope",
    "current_user_id",
    "current_tenant_id",
    "current_branch_id",
    "current_scope",
]

"""User long-term memory management — the HTTP surface for viewing/deleting
the facts ``MemoryTool.remember()`` saves (see ``serving/factory
.py::build_memory_tool()`` for how those get keyed by user, not thread).

Routes:
  GET    /me/memories       – list this user's standing facts/preferences
  DELETE /me/memories/{id}  – delete one
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from substrate.kernel.abstractions.storage.memory import MemoryNamespace, MemoryQuery
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.security.deps import AuthClaims, get_current_user

router = APIRouter(prefix="/me/memories", tags=["memory"])


def _caller(user: AuthClaims) -> MemoryNamespace:
    """Who is asking: the authenticated user, in their tenant."""
    return MemoryNamespace(tenant_id=user.tenant_id or "default", user_id=user.sub)


class MemoryOut(BaseModel):
    id: str
    content: str


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    ctx: ServerDependencies = Depends(get_ctx),
    user: AuthClaims = Depends(get_current_user),
) -> list[MemoryOut]:
    if ctx.long_term_memory is None:
        return []
    # The user's own facts — what ``MemoryTool.remember()`` saved for them (see also
    # serving/factory.py::build_user_memory_context_block()). Tenant-level facts are visible
    # to them too, but they are not the user's to list for deletion.
    matches = await ctx.long_term_memory.query(MemoryQuery(namespace=_caller(user), limit=100))
    return [MemoryOut(id=m.id, content=m.text) for m in matches if m.record.namespace.user_id == user.sub]


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    ctx: ServerDependencies = Depends(get_ctx),
    user: AuthClaims = Depends(get_current_user),
) -> None:
    if ctx.long_term_memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    deleted = await ctx.long_term_memory.delete(_caller(user), memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")

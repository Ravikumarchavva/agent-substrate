"""User long-term memory management — the HTTP surface for viewing/deleting
the facts ``MemoryTool.remember()`` saves (see ``infrastructure/serving_factory
.py::build_memory_tool()`` for how those get keyed by user, not thread).

Routes:
  GET    /me/memories       – list this user's standing facts/preferences
  DELETE /me/memories/{id}  – delete one
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.security.deps import AuthClaims, get_current_user

router = APIRouter(prefix="/me/memories", tags=["memory"])


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
    from substrate.kernel.core.identity import AgentId

    # No namespace override: MemoryTool.remember() never passes one, so
    # every fact lands in DurableMemoryStore's default namespace — read from
    # the same place things are actually written to (see also
    # infrastructure/serving_factory.py::build_user_memory_context_block()).
    memories = await ctx.long_term_memory.list_all(
        AgentId(type="user", key=user.sub), limit=100
    )
    return [MemoryOut(id=m.id, content=m.content) for m in memories]


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    ctx: ServerDependencies = Depends(get_ctx),
    user: AuthClaims = Depends(get_current_user),
) -> None:
    if ctx.long_term_memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    from substrate.kernel.core.identity import AgentId

    deleted = await ctx.long_term_memory.delete(
        AgentId(type="user", key=user.sub), memory_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")

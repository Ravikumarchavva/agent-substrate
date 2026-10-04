"""User long-term memory management — the HTTP surface for viewing/deleting
the facts ``MemoryTool.remember()`` saves (see ``substrate_cloud/factory
.py::build_memory_tool()`` for how those get keyed by user, not thread).

Routes:
  GET    /me/memories       – list this user's standing facts/preferences
  POST   /me/memories       – add one the user wants remembered
  PATCH  /me/memories/{id}  – reword one
  DELETE /me/memories/{id}  – delete one
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from substrate.stores import (
    MemoryCategory,
    MemoryNamespace,
    MemoryQuery,
    MemoryRecord,
)
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user

router = APIRouter(prefix="/me/memories", tags=["memory"])


def _caller(user: AuthClaims) -> MemoryNamespace:
    """Who is asking: the authenticated user, in their tenant."""
    return MemoryNamespace(tenant_id=user.tenant_id or "default", user_id=user.sub)


MAX_MEMORIES = 100
"""What one user may keep. ``list_memories`` shows this many, so the cap keeps every memory visible (and deletable)."""

MAX_MEMORY_CHARS = 500


class MemoryOut(BaseModel):
    id: str
    content: str


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CHARS)


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    ctx: ServerDependencies = Depends(get_ctx),
    user: AuthClaims = Depends(get_current_user),
) -> list[MemoryOut]:
    if ctx.long_term_memory is None:
        return []
    # The user's own facts — what ``MemoryTool.remember()`` saved for them (see also
    # substrate_cloud/factory.py::build_user_memory_context_block()). Tenant-level facts are visible
    # to them too, but they are not the user's to list for deletion.
    matches = await ctx.long_term_memory.query(
        MemoryQuery(namespace=_caller(user), limit=100)
    )
    return [
        MemoryOut(id=m.id, content=m.text)
        for m in matches
        if m.record.namespace.user_id == user.sub
    ]


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


def _text(body: MemoryIn) -> str:
    text = " ".join(body.content.split())
    if not text:
        raise HTTPException(status_code=422, detail="A memory cannot be empty.")
    return text


@router.post("", response_model=MemoryOut, status_code=201)
async def add_memory(
    body: MemoryIn,
    ctx: ServerDependencies = Depends(get_ctx),
    user: AuthClaims = Depends(get_current_user),
) -> MemoryOut:
    if ctx.long_term_memory is None:
        raise HTTPException(status_code=503, detail="Memory is not available")
    text = _text(body)
    existing = await ctx.long_term_memory.query(
        MemoryQuery(namespace=_caller(user), limit=MAX_MEMORIES + 1)
    )
    mine = [m for m in existing if m.record.namespace.user_id == user.sub]
    if len(mine) >= MAX_MEMORIES:
        raise HTTPException(
            status_code=409,
            detail=f"You can keep {MAX_MEMORIES} memories. Delete one to add another.",
        )
    if any(m.text.strip().lower() == text.lower() for m in mine):
        raise HTTPException(status_code=409, detail="That is already remembered.")
    record = MemoryRecord.from_text(
        text, namespace=_caller(user), category=MemoryCategory.SEMANTIC
    )
    await ctx.long_term_memory.save(record)
    return MemoryOut(id=record.id, content=text)


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: str,
    body: MemoryIn,
    ctx: ServerDependencies = Depends(get_ctx),
    user: AuthClaims = Depends(get_current_user),
) -> MemoryOut:
    if ctx.long_term_memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    caller = _caller(user)
    existing = await ctx.long_term_memory.get(caller, memory_id)
    # Only the user's own memory: a tenant-level fact is visible to them but not theirs to reword.
    if existing is None or not existing.namespace.owned_by(caller):
        raise HTTPException(status_code=404, detail="Memory not found")
    text = _text(body)
    reworded = MemoryRecord.from_text(
        text,
        id=existing.id,
        namespace=existing.namespace,
        category=existing.category,
        provenance=existing.provenance,
    )
    await ctx.long_term_memory.save(reworded)
    return MemoryOut(id=reworded.id, content=text)

"""Thread (session) CRUD endpoints.

Routes:
  POST   /threads              – create thread
  GET    /threads              – list threads (pinned first; ?archived=true for the archive)
  GET    /threads/search?q=    – find messages across the caller's threads
  GET    /threads/{id}         – get thread
  PATCH  /threads/{id}         – update thread
  DELETE /threads/{id}         – delete thread
  GET    /threads/{id}/messages – get thread messages
  GET    /threads/{id}/export  – download as Markdown or JSON
  GET/POST/DELETE /threads/{id}/share – a read-only public link
"""

from __future__ import annotations

import json
import secrets
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.models import Thread, ThreadShare

from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db
from substrate_cloud.monolith.schemas import (
    ThreadCreate,
    ThreadOut,
    ThreadUpdate,
)
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.monolith.services import (
    create_thread,
    delete_thread,
    get_owned_thread,
    list_threads,
    update_thread,
)
from substrate_cloud.monolith.services.thread_service import (
    owned_thread_ids,
    thread_row,
)
from substrate_cloud.stream import project_thread
from substrate_cloud.stream.runs import RunDetail, inspect_thread
from substrate_cloud.stream.export import filename, messages_of, to_markdown
from substrate_cloud.stream.search import message_counts, search_messages

MAX_PAGE = 100

router = APIRouter(
    prefix="/threads",
    tags=["threads"],
)


@router.post("", response_model=ThreadOut, status_code=201)
async def create_thread_endpoint(
    body: ThreadCreate,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Create a new chat thread owned by the caller."""
    thread = await create_thread(
        db,
        name=body.name or "New Chat",
        user_identifier=user.sub,
        tenant_id=user.tenant_id,
    )
    return ThreadOut(**thread_row(thread))


@router.get("", response_model=List[ThreadOut])
async def list_threads_endpoint(
    request: Request,
    limit: int = Query(50, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
    archived: bool = False,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """List the caller's threads, pinned first then newest first, a page at a time. ``archived=true`` lists the archive instead."""
    rows = await list_threads(
        db,
        user_identifier=None if user.is_admin else user.sub,
        limit=limit,
        offset=offset,
        archived=archived,
    )
    store = getattr(request.app.state, "store", None)
    if store is not None and rows:
        counts = await message_counts(
            store,
            tenant=user.tenant_id,
            thread_ids=[str(r["id"]) for r in rows],
        )
        for row in rows:
            row["message_count"] = counts.get(str(row["id"]), 0)
    return [ThreadOut(**row) for row in rows]


class SearchHit(BaseModel):
    thread_id: uuid.UUID
    thread_name: Optional[str] = None
    role: str
    snippet: str


@router.get("/search", response_model=List[SearchHit])
async def search_threads_endpoint(
    request: Request,
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Messages containing ``q`` in the caller's own conversations (archived included), newest first. Titles are matched by the sidebar."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        return []
    ids = await owned_thread_ids(db, user_identifier=user.sub)
    hits = await search_messages(
        store,
        tenant=user.tenant_id,
        thread_ids=[str(i) for i in ids],
        query=q,
        limit=limit,
    )
    names = {}
    if hits:
        rows = await db.execute(
            select(Thread.id, Thread.name).where(
                Thread.id.in_({uuid.UUID(h.thread_id) for h in hits})
            )
        )
        names = {str(r.id): r.name for r in rows}
    return [
        SearchHit(
            thread_id=uuid.UUID(h.thread_id),
            thread_name=names.get(h.thread_id),
            role=h.role,
            snippet=h.snippet,
        )
        for h in hits
    ]


@router.get("/{thread_id}", response_model=ThreadOut)
async def get_thread_endpoint(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Get a single thread by ID."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return ThreadOut(**thread_row(thread))


@router.patch("/{thread_id}", response_model=ThreadOut)
async def update_thread_endpoint(
    thread_id: uuid.UUID,
    body: ThreadUpdate,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Update thread name, tags, or metadata."""
    if not await get_owned_thread(db, thread_id, user):
        raise HTTPException(status_code=404, detail="Thread not found")
    thread = await update_thread(
        db,
        thread_id,
        name=body.name,
        tags=body.tags,
        metadata=body.metadata,
        pinned=body.pinned,
        archived=body.archived,
    )
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return ThreadOut(**thread_row(thread))


@router.delete("/{thread_id}", status_code=204)
async def delete_thread_endpoint(
    thread_id: uuid.UUID,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Delete a thread — a soft delete (see thread_service.py::delete_thread):
    hidden from this user, but the row and its files are retained, not
    erased. Permanent erasure is a distinct GDPR-erasure action."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    deleted = await delete_thread(db, thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Thread not found")


@router.get("/{thread_id}/messages")
async def get_thread_messages(
    thread_id: uuid.UUID,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
) -> List[dict]:
    """Return a thread's full conversation, projected from the EventLogProtocol.

    The EventLogProtocol is the single source of truth — there is no separate,
    independently-written history table. This concatenates every run ever
    submitted for this thread (oldest first) through the same
    ``wire_from_log`` mapping that powers live streaming (``POST /chat``)
    and reconnect (``GET /stream/{thread_id}``): all three are views over
    the same underlying data.

    Returns a flat list of wire events (``{"type": "user.message", ...}``,
    ``{"type": "text.delta", ...}``, ``{"type": "tool.call", ...}``, etc.)
    rather than the ``StepOut`` shape this endpoint used to return — a
    client folds these into displayed messages the same way it folds a
    live SSE stream, since they're the same event vocabulary.
    """
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    runtime = ctx.runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not configured")

    events = await project_thread(runtime.store, str(thread_id))
    return [event.model_dump(mode="json") for event in events]


@router.get("/{thread_id}/export")
async def export_thread_endpoint(
    thread_id: uuid.UUID,
    format: str = Query("md", pattern="^(md|json)$"),
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Download a conversation: ``md`` (readable) or ``json`` (the messages as data). Only what was said, not tool traces."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if ctx.runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not configured")
    events = await project_thread(ctx.runtime.store, str(thread_id))
    messages = messages_of([e.model_dump(mode="json") for e in events])
    title = thread.name or "Conversation"
    if format == "json":
        body = json.dumps({"title": title, "messages": messages}, indent=2)
        media = "application/json"
    else:
        body = to_markdown(title, messages)
        media = "text/markdown; charset=utf-8"
    return Response(
        content=body,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{filename(title, format)}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{thread_id}/runs", response_model=List[RunDetail])
async def get_thread_runs(
    thread_id: uuid.UUID,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """What the conversation's runs did: time, model calls, tokens, cost, tools and their outcome. For the person who owns it."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if ctx.runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not configured")
    return await inspect_thread(ctx.runtime.store, str(thread_id))


class ShareOut(BaseModel):
    token: str


async def _share_of(db: AsyncSession, thread_id: uuid.UUID) -> Optional[ThreadShare]:
    row = await db.execute(
        select(ThreadShare).where(ThreadShare.thread_id == thread_id)
    )
    return row.scalars().first()


@router.get("/{thread_id}/share", response_model=Optional[ShareOut])
async def get_share_endpoint(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """The conversation's public link token, or ``null`` if it is not shared."""
    if not await get_owned_thread(db, thread_id, user):
        raise HTTPException(status_code=404, detail="Thread not found")
    share = await _share_of(db, thread_id)
    return ShareOut(token=share.token) if share else None


@router.post("/{thread_id}/share", response_model=ShareOut, status_code=201)
async def create_share_endpoint(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Make the conversation readable by anyone with the link (read-only: what was said, nothing else). Idempotent."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    share = await _share_of(db, thread_id)
    if share is None:
        share = ThreadShare(
            token=secrets.token_urlsafe(32),
            thread_id=thread_id,
            tenant_id=thread.tenant_id or user.tenant_id,
            name=thread.name,
        )
        db.add(share)
        await db.flush()
    return ShareOut(token=share.token)


@router.delete("/{thread_id}/share", status_code=204)
async def delete_share_endpoint(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Stop sharing: the link stops working at once."""
    if not await get_owned_thread(db, thread_id, user):
        raise HTTPException(status_code=404, detail="Thread not found")
    share = await _share_of(db, thread_id)
    if share is not None:
        await db.delete(share)
        await db.flush()

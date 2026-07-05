"""Service layer for thread (session) and feedback CRUD operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.serving.monolith.models import Thread, Feedback
from substrate.serving.shared.auth.claims import AuthClaims


# ── Thread CRUD ──────────────────────────────────────────────────────────────


async def create_thread(
    db: AsyncSession,
    *,
    name: str = "New Chat",
    user_id: Optional[uuid.UUID] = None,
    user_identifier: Optional[str] = None,
    tenant_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Thread:
    """Create a new thread (chat session)."""
    thread = Thread(
        name=name,
        user_id=user_id,
        user_identifier=user_identifier,
        tenant_id=tenant_id,
        tags=tags or [],
        metadata_=metadata or {},
    )
    db.add(thread)
    await db.flush()
    return thread


async def get_thread(db: AsyncSession, thread_id: uuid.UUID) -> Optional[Thread]:
    """Get a thread by ID — NO ownership check.

    Only for internal/background callers that have no request identity
    (e.g. scheduled tasks operating on their own tagged threads).  Every
    route that resolves a caller-supplied thread_id must use
    ``get_owned_thread`` instead.
    """
    result = await db.execute(select(Thread).where(Thread.id == thread_id))
    return result.scalar_one_or_none()


async def get_owned_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
    claims: AuthClaims,
) -> Optional[Thread]:
    """Get a thread by ID, enforcing that the caller owns it.

    Returns ``None`` both when the thread does not exist and when it belongs
    to another user or tenant, so routes 404 identically and never leak
    existence.

    Ownership = ``thread.user_identifier == claims.sub`` AND
    ``thread.tenant_id == claims.tenant_id`` (the frontend's stable user id
    and tenant namespace carried in the JWT).  Admins bypass the check
    entirely — an admin's ``tenant_id`` is not assumed to match every
    tenant's threads.

    Migration affordance: threads created before ownership/tenant stamping
    existed have ``user_identifier``/``tenant_id IS NULL``; the first
    authenticated user to access one claims it (stamped in place, both
    fields together — a legacy thread can't be claimed into a mismatched
    tenant). New threads are always created with an owner and tenant, so
    this branch only fires for legacy rows.
    """
    thread = await get_thread(db, thread_id)
    if thread is None:
        return None
    if claims.is_admin:
        return thread
    owned = thread.user_identifier == claims.sub
    same_tenant = thread.tenant_id == claims.tenant_id
    if owned and same_tenant:
        return thread
    if owned and thread.tenant_id is None:
        thread.tenant_id = claims.tenant_id
        await db.flush()
        return thread
    if thread.user_identifier is None and thread.tenant_id is None:
        thread.user_identifier = claims.sub
        thread.tenant_id = claims.tenant_id
        await db.flush()
        return thread
    return None


async def list_threads(
    db: AsyncSession,
    *,
    user_id: Optional[uuid.UUID] = None,
    user_identifier: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List threads with message counts.

    ``user_identifier`` scopes the list to threads owned by that user, plus
    unowned legacy rows (``user_identifier IS NULL`` — claimable on access,
    see ``get_owned_thread``).

    Message counts come from the EventLog (``substrate_run_queue`` joined to
    ``substrate_event_log``), not a separate steps table — see
    ``routes/admin.py::list_all_threads`` for the same join pattern.
    """
    query = (
        select(Thread).order_by(Thread.updated_at.desc()).limit(limit).offset(offset)
    )

    # Exclude scheduled tasks threads from regular recent threads list
    query = query.where(
        (Thread.tags == None) | (~Thread.tags.contains(["scheduled_task"]))  # noqa: E711
    )

    if user_id:
        query = query.where(Thread.user_id == user_id)

    if user_identifier is not None:
        query = query.where(
            (Thread.user_identifier == user_identifier)
            | (Thread.user_identifier == None)  # noqa: E711 — SQLAlchemy IS NULL
        )

    result = await db.execute(query)
    threads = list(result.scalars().all())
    if not threads:
        return []

    thread_ids = [str(t.id) for t in threads]
    count_rows = (
        await db.execute(
            text(
                """
                SELECT rq.thread_id AS thread_id, COUNT(el.*) AS message_count
                FROM substrate_run_queue rq
                JOIN substrate_event_log el ON el.run_id = rq.run_id
                WHERE rq.thread_id = ANY(:thread_ids)
                GROUP BY rq.thread_id
                """
            ),
            {"thread_ids": thread_ids},
        )
    ).all()
    counts = {r.thread_id: r.message_count for r in count_rows}

    return [
        {
            "id": thread.id,
            "name": thread.name,
            "user_id": thread.user_id,
            "tags": thread.tags,
            "metadata": thread.metadata_,
            "created_at": thread.created_at,
            "updated_at": thread.updated_at,
            "message_count": counts.get(str(thread.id), 0),
        }
        for thread in threads
    ]


async def update_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
    *,
    name: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Thread]:
    """Update thread metadata."""
    values: Dict[str, Any] = {}
    if name is not None:
        values["name"] = name
    if tags is not None:
        values["tags"] = tags
    if metadata is not None:
        values["metadata_"] = metadata

    if not values:
        return await get_thread(db, thread_id)

    values["updated_at"] = datetime.now(timezone.utc)

    await db.execute(update(Thread).where(Thread.id == thread_id).values(**values))
    await db.flush()
    return await get_thread(db, thread_id)


async def delete_thread(db: AsyncSession, thread_id: uuid.UUID) -> bool:
    """Delete a thread and its feedbacks (cascade)."""
    result = await db.execute(delete(Thread).where(Thread.id == thread_id))
    return bool(getattr(result, "rowcount", 0))


# ── Feedback CRUD ────────────────────────────────────────────────────────────


async def create_feedback(
    db: AsyncSession,
    *,
    for_id: uuid.UUID,
    thread_id: uuid.UUID,
    value: int,
    comment: Optional[str] = None,
) -> Feedback:
    """Create feedback on a step."""
    feedback = Feedback(
        for_id=for_id,
        thread_id=thread_id,
        value=value,
        comment=comment,
    )
    db.add(feedback)
    await db.flush()
    return feedback

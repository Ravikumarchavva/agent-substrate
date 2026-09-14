"""Service layer for thread (session) and feedback CRUD operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text, update
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

    Every thread is created with an owner and tenant stamped
    (``routes/threads.py::create_thread_endpoint`` — login is required, so
    there is no anonymous/unauthenticated creation path), so ownership is a
    straight equality check with no NULL-claiming affordance for legacy
    unowned rows.
    """
    thread = await get_thread(db, thread_id)
    if thread is None:
        return None
    if claims.role == "platform_admin":
        # Platform-wide bypass only — a tenant_admin still must not read
        # another tenant's threads, checked below same as anyone else.
        return thread
    owned = thread.user_identifier == claims.sub
    same_tenant = thread.tenant_id == claims.tenant_id
    if claims.is_admin and same_tenant:
        # tenant_admin: any thread within its own tenant, not just its own.
        return thread
    if owned and same_tenant:
        # Soft-deleted (see delete_thread below) — gone for its owner, but
        # never hard-erased, so a safety/abuse review still has it via the
        # admin path above. 404, not a distinct "deleted" error: the owner
        # deleted it, so from their side it simply no longer exists.
        if thread.deleted_at is not None:
            return None
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

    ``user_identifier`` scopes the list to threads owned by that user.
    Login is required to create a thread (see ``get_owned_thread``), so
    there is no unowned-row case to special-case here.

    Message counts come from the EventLogProtocol (``substrate_run_queue`` joined to
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

    # Soft-deleted threads (see delete_thread) are gone for their owner but
    # deliberately not hard-erased — excluded here, still visible to a
    # separate admin/audit path.
    query = query.where(Thread.deleted_at.is_(None))

    if user_id:
        query = query.where(Thread.user_id == user_id)

    if user_identifier is not None:
        query = query.where(Thread.user_identifier == user_identifier)

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
            "locked_at": thread.locked_at,
            "locked_reason": thread.locked_reason,
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
    """Soft-delete a thread — hidden from its owner (excluded by
    ``list_threads``, 404s via ``get_owned_thread``) but the row and its
    storage are retained, not erased.

    A user's own "delete this chat" and GDPR erasure are deliberately
    different operations with different retention obligations: if this were
    a hard delete, a policy-violating request could be permanently wiped
    from existence by the same person who made it, taking any trust &
    safety review trail with it. Permanent erasure stays exclusively
    ``capabilities/gdpr/eraser.py``'s job — a distinct, deliberate action,
    not a side effect of tidying up the sidebar.
    """
    thread = await get_thread(db, thread_id)
    if thread is None:
        return False
    thread.deleted_at = datetime.now(timezone.utc)
    thread.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return True


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

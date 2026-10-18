"""Conversation Service: business logic.

Every function takes the caller's claims: a thread is found only under the caller's tenant, and for the owner only (a tenant or
platform administrator sees the tenant's, a platform administrator everyone's, as in the monolith). Someone else's thread is
``None``, so a route answers 404 and never reveals that it exists.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.services.conversation.models import Feedback, Thread
from substrate_cloud.shared.auth.claims import AuthClaims


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_thread(
    db: AsyncSession, claims: AuthClaims, *, name: str = "New Chat"
) -> Thread:
    thread = Thread(
        name=name,
        tenant_id=claims.tenant_id,
        user_identifier=claims.sub,
        tags=[],
        metadata_={},
    )
    db.add(thread)
    await db.flush()
    await db.refresh(thread)
    return thread


async def get_owned_thread(
    db: AsyncSession, thread_id: uuid.UUID, claims: AuthClaims
) -> Optional[Thread]:
    thread = (
        await db.execute(select(Thread).where(Thread.id == thread_id))
    ).scalar_one_or_none()
    if thread is None:
        return None
    if claims.role == "platform_admin":
        return thread
    if thread.tenant_id != claims.tenant_id:
        return None
    if claims.is_admin:
        return thread
    if thread.user_identifier != claims.sub or thread.deleted_at is not None:
        return None
    return thread


async def list_threads(
    db: AsyncSession,
    claims: AuthClaims,
    *,
    limit: int = 50,
    offset: int = 0,
    archived: bool = False,
) -> List[Thread]:
    """The caller's threads, pinned first then newest first."""
    query = select(Thread).where(
        Thread.tenant_id == claims.tenant_id,
        Thread.deleted_at.is_(None),
        Thread.archived_at.is_not(None) if archived else Thread.archived_at.is_(None),
    )
    if not claims.is_admin:
        query = query.where(Thread.user_identifier == claims.sub)
    query = (
        query.order_by(
            Thread.pinned_at.is_(None),
            Thread.pinned_at.desc(),
            Thread.updated_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(query)).scalars().all())


async def update_thread(
    db: AsyncSession,
    thread: Thread,
    *,
    name: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    pinned: Optional[bool] = None,
    archived: Optional[bool] = None,
) -> Thread:
    if name is not None:
        thread.name = name
    if tags is not None:
        thread.tags = tags
    if metadata is not None:
        thread.metadata_ = metadata
    if pinned is not None:
        thread.pinned_at = _now() if pinned else None
    if archived is not None:
        thread.archived_at = _now() if archived else None
    thread.updated_at = _now()
    await db.flush()
    return thread


async def delete_thread(db: AsyncSession, thread: Thread) -> None:
    thread.deleted_at = _now()
    await db.flush()


def thread_row(thread: Thread, message_count: int = 0) -> Dict[str, Any]:
    """A thread as the API returns it (``ThreadOut``)."""
    return {
        "id": thread.id,
        "name": thread.name,
        "user_id": None,
        "tags": thread.tags,
        "metadata": thread.metadata_,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
        "message_count": message_count,
        "locked_at": None,
        "locked_reason": None,
        "pinned_at": thread.pinned_at,
        "archived_at": thread.archived_at,
        "agent_id": None,
    }


# ── Feedback ─────────────────────────────────────────────────────────────────


async def set_feedback(
    db: AsyncSession,
    *,
    for_id: uuid.UUID,
    thread_id: uuid.UUID,
    value: int,
    comment: Optional[str] = None,
) -> Optional[Feedback]:
    """One rating per answer, the latest wins; ``0`` takes it back (None)."""
    existing = (
        await db.execute(
            select(Feedback).where(
                Feedback.for_id == for_id, Feedback.thread_id == thread_id
            )
        )
    ).scalar_one_or_none()
    if value == 0:
        if existing is not None:
            await db.execute(delete(Feedback).where(Feedback.id == existing.id))
        return None
    if existing is None:
        existing = Feedback(for_id=for_id, thread_id=thread_id)
        db.add(existing)
    existing.thread_id, existing.value, existing.comment = thread_id, value, comment
    await db.flush()
    return existing


async def list_feedback(db: AsyncSession, thread_id: uuid.UUID) -> List[Feedback]:
    return list(
        (
            await db.execute(
                select(Feedback)
                .where(Feedback.thread_id == thread_id)
                .order_by(Feedback.created_at)
            )
        )
        .scalars()
        .all()
    )

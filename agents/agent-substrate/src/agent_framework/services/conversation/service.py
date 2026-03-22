"""Conversation Service — business logic.

Extracted from monolith server/services/__init__.py. Pure async
functions that operate on the Conversation models.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.services.conversation.models import Feedback, Step, Thread


# ── Thread CRUD ──────────────────────────────────────────────────────────────


async def create_thread(
    db: AsyncSession,
    *,
    name: str = "New Chat",
    user_id: Optional[uuid.UUID] = None,
    user_identifier: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Thread:
    thread = Thread(
        name=name,
        user_id=user_id,
        user_identifier=user_identifier,
        tags=tags or [],
        metadata_=metadata or {},
    )
    db.add(thread)
    await db.flush()
    return thread


async def get_thread(db: AsyncSession, thread_id: uuid.UUID) -> Optional[Thread]:
    result = await db.execute(select(Thread).where(Thread.id == thread_id))
    return result.scalar_one_or_none()


async def list_threads(
    db: AsyncSession,
    *,
    user_id: Optional[uuid.UUID] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    count_subq = (
        select(Step.thread_id, func.count(Step.id).label("message_count"))
        .where(Step.type.in_(["user_message", "assistant_message"]))
        .group_by(Step.thread_id)
        .subquery()
    )

    query = (
        select(
            Thread, func.coalesce(count_subq.c.message_count, 0).label("message_count")
        )
        .outerjoin(count_subq, Thread.id == count_subq.c.thread_id)
        .order_by(Thread.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )

    if user_id:
        query = query.where(Thread.user_id == user_id)

    result = await db.execute(query)
    rows = result.all()

    return [
        {
            "id": str(thread.id),
            "name": thread.name,
            "user_id": str(thread.user_id) if thread.user_id else None,
            "tags": thread.tags,
            "metadata": thread.metadata_,
            "created_at": thread.created_at.isoformat(),
            "updated_at": thread.updated_at.isoformat(),
            "message_count": msg_count,
        }
        for thread, msg_count in rows
    ]


async def update_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
    *,
    name: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Thread]:
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
    result = await db.execute(delete(Thread).where(Thread.id == thread_id))
    return result.rowcount > 0


# ── Step CRUD ────────────────────────────────────────────────────────────────


async def create_step(
    db: AsyncSession,
    *,
    thread_id: uuid.UUID,
    type: str,
    name: str = "",
    parent_id: Optional[uuid.UUID] = None,
    input: Optional[str] = None,
    output: Optional[str] = None,
    streaming: bool = False,
    is_error: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    generation: Optional[Dict[str, Any]] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> Step:
    step = Step(
        thread_id=thread_id,
        type=type,
        name=name,
        parent_id=parent_id,
        input=input,
        output=output,
        streaming=streaming,
        is_error=is_error,
        metadata_=metadata or {},
        generation=generation,
        start_time=start_time,
        end_time=end_time,
    )
    db.add(step)
    await db.flush()

    await db.execute(
        update(Thread)
        .where(Thread.id == thread_id)
        .values(updated_at=datetime.now(timezone.utc))
    )
    return step


async def get_steps(
    db: AsyncSession,
    thread_id: uuid.UUID,
    *,
    types: Optional[List[str]] = None,
) -> List[Step]:
    query = select(Step).where(Step.thread_id == thread_id).order_by(Step.created_at)
    if types:
        query = query.where(Step.type.in_(types))

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_step(db: AsyncSession, step_id: uuid.UUID) -> Optional[Step]:
    result = await db.execute(select(Step).where(Step.id == step_id))
    return result.scalar_one_or_none()


async def update_step(
    db: AsyncSession,
    step_id: uuid.UUID,
    *,
    output: Optional[str] = None,
    streaming: Optional[bool] = None,
    is_error: Optional[bool] = None,
    end_time: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
    generation: Optional[Dict[str, Any]] = None,
) -> Optional[Step]:
    values: Dict[str, Any] = {}
    if output is not None:
        values["output"] = output
    if streaming is not None:
        values["streaming"] = streaming
    if is_error is not None:
        values["is_error"] = is_error
    if end_time is not None:
        values["end_time"] = end_time
    if metadata is not None:
        values["metadata_"] = metadata
    if generation is not None:
        values["generation"] = generation

    if not values:
        return await get_step(db, step_id)

    await db.execute(update(Step).where(Step.id == step_id).values(**values))
    await db.flush()
    return await get_step(db, step_id)


# ── Feedback CRUD ────────────────────────────────────────────────────────────


async def create_feedback(
    db: AsyncSession,
    *,
    for_id: uuid.UUID,
    thread_id: uuid.UUID,
    value: int,
    comment: Optional[str] = None,
) -> Feedback:
    feedback = Feedback(
        for_id=for_id,
        thread_id=thread_id,
        value=value,
        comment=comment,
    )
    db.add(feedback)
    await db.flush()
    return feedback


# ── Memory helpers ───────────────────────────────────────────────────────────


async def load_messages_for_memory(
    db: AsyncSession,
    thread_id: uuid.UUID,
) -> List[Dict[str, Any]]:
    """Load steps as dicts suitable for reconstructing agent memory."""
    steps = await get_steps(
        db,
        thread_id,
        types=["user_message", "assistant_message", "tool_call", "tool_result"],
    )
    return [
        {
            "type": step.type,
            "input": step.input,
            "output": step.output,
            "metadata": step.metadata_,
            "created_at": step.created_at.isoformat() if step.created_at else None,
        }
        for step in steps
    ]

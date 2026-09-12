"""Single orchestration point for tenant-scoped personal-data erasure."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.capabilities.storage.layout import tenant_prefix, user_prefix
from substrate.serving.monolith.models import FileMetadata, Thread, User


@dataclass(frozen=True, slots=True)
class ErasureSummary:
    tenant_id: str
    user_id: str | None
    conversations_deleted: int
    metadata_rows_deleted: int
    objects_deleted: int
    redis_keys_deleted: int

    def as_dict(self) -> dict[str, int | str | None]:
        return asdict(self)


async def _delete_prefix(store: Any, prefix: str) -> int:
    method = getattr(store, "delete_prefix", None)
    if method is None:
        raise RuntimeError("configured file store does not support prefix erasure")
    return int(await method(prefix))


async def _redis_sweep(redis: Any, identifiers: set[str]) -> int:
    if redis is None or not identifiers:
        return 0
    deleted = 0
    # Redis namespace formats vary by capability. Restrict deletion to keys
    # containing an already-owned user or conversation id; never use KEYS.
    async for raw_key in redis.scan_iter(match="*"):
        key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
        if any(identifier in key for identifier in identifiers):
            deleted += int(await redis.delete(raw_key))
    return deleted


async def erase_user(
    db: AsyncSession, *, store: Any, redis: Any, tenant_id: str, user_id: str
) -> ErasureSummary:
    threads = list(
        (
            await db.execute(
                select(Thread).where(
                    Thread.tenant_id == tenant_id,
                    Thread.user_identifier == user_id,
                )
            )
        ).scalars()
    )
    thread_ids = {str(thread.id) for thread in threads}
    # `user_id` is the caller's own sub (the same string threads.user_identifier
    # stores) — a `users` row only ever exists under its UUID primary key when
    # that sub happens to parse as one (see routes/files.py::_ensure_user);
    # non-UUID subs (e.g. an external platform's own id format) have no
    # substrate `users` row to delete at all, only the Thread/FileMetadata
    # rows above, which are keyed by the raw string regardless.
    try:
        user_uuid: uuid.UUID | None = uuid.UUID(user_id)
    except ValueError:
        user_uuid = None
    ownership = [FileMetadata.thread_id.in_(thread_ids)] if thread_ids else []
    if user_uuid is not None:
        ownership.append(FileMetadata.user_id == user_uuid)
    metadata_result = await db.execute(
        delete(FileMetadata).where(
            FileMetadata.org_id == tenant_id,
            or_(*ownership) if ownership else False,
        )
    )
    # Thread deletion cascades elements, feedback and scheduled-task rows.
    if thread_ids:
        await db.execute(delete(Thread).where(Thread.id.in_(thread_ids)))
    if user_uuid is not None:
        await db.execute(delete(User).where(User.id == user_uuid))
    await db.commit()
    objects = await _delete_prefix(store, user_prefix(tenant_id, user_id))
    for conversation_id in thread_ids:
        objects += await _delete_prefix(
            store, f"{tenant_prefix(tenant_id)}/conversations/{conversation_id}"
        )
    redis_deleted = await _redis_sweep(redis, {user_id, *thread_ids})
    return ErasureSummary(
        tenant_id,
        user_id,
        len(thread_ids),
        metadata_result.rowcount or 0,
        objects,
        redis_deleted,
    )


async def erase_tenant(
    db: AsyncSession, *, store: Any, redis: Any, tenant_id: str
) -> ErasureSummary:
    threads = list(
        (
            await db.execute(select(Thread).where(Thread.tenant_id == tenant_id))
        ).scalars()
    )
    thread_ids = {str(thread.id) for thread in threads}
    users = {thread.user_identifier for thread in threads if thread.user_identifier}
    metadata_result = await db.execute(
        delete(FileMetadata).where(FileMetadata.org_id == tenant_id)
    )
    await db.execute(delete(Thread).where(Thread.tenant_id == tenant_id))
    await db.commit()
    objects = await _delete_prefix(store, tenant_prefix(tenant_id))
    redis_deleted = await _redis_sweep(redis, thread_ids | users)
    return ErasureSummary(
        tenant_id,
        None,
        len(thread_ids),
        metadata_result.rowcount or 0,
        objects,
        redis_deleted,
    )

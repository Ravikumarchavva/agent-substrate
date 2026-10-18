"""Single orchestration point for tenant-scoped personal-data erasure.

A deletion request has to reach every place the person's data lives — the relational rows,
the file store, Redis, the session index, the long-term memory, and the run journal that holds
the raw conversation. Leaving out any one of them means the request was not satisfied.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.stores import Erased, MemoryNamespace, Store
from substrate.workspace.layout import tenant_prefix, user_prefix
from substrate.types import Actor
from substrate_cloud.monolith.models import (
    Agent,
    Group,
    GroupMember,
    Thread,
    User,
)
from substrate_cloud.monolith.services.groups.service import member_actor


@dataclass(frozen=True, slots=True)
class ErasureSummary:
    tenant_id: str
    user_id: str | None
    conversations_deleted: int
    objects_deleted: int
    redis_keys_deleted: int
    documents_deleted: int
    memories_deleted: int = 0
    runs_deleted: int = 0
    thread_nodes_deleted: int = 0
    task_boards_deleted: int = 0
    vectors_deleted: int = 0
    graph_entities_deleted: int = 0
    groups_deleted: int = 0
    agents_deleted: int = 0

    def as_dict(self) -> dict[str, int | str | None]:
        return asdict(self)


async def _delete_prefix(store: Any, prefix: str) -> int:
    method = getattr(store, "delete_prefix", None)
    if method is None:
        raise RuntimeError("configured file store does not support prefix erasure")
    return int(await method(prefix))


async def _erase_documents(folder: Store | None, store: Any, prefix: str) -> int:
    """The documents (catalog rows, bundles) the conversations under ``prefix`` were given — see ``substrate.documents.Library``."""
    if folder is None:
        return 0
    from substrate.documents import Library

    return await Library(folder, files=store).erase_under(prefix)


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


async def _erase_groups(
    db: AsyncSession,
    *,
    groups: list[Group],
    runtime_store: Any,
    folder: Store | None,
    tenant_id: str,
) -> int:
    """What the user's groups hold: what was said (the channel), what each member did about it (its runs, its conversation, its budget).
    Must run while the membership rows still exist, as they say who the members are."""
    for group in groups:
        members = (
            (
                await db.execute(
                    select(GroupMember).where(GroupMember.group_id == group.id)
                )
            )
            .scalars()
            .all()
        )
        actors: list[Actor] = [member_actor(m.agent_id, group.id) for m in members]
        if runtime_store is not None:
            await runtime_store.channel_delete(group.channel)
            for actor in actors:
                await runtime_store.erase(tenant=tenant_id, agent=actor)
        if folder is not None:
            for actor in actors:
                await folder.tenant(tenant_id).erase_conversation(actor.key)
    return len(groups)


async def erase_user(
    db: AsyncSession,
    *,
    store: Any,
    redis: Any,
    tenant_id: str,
    user_id: str,
    cfg: Any,
    memory_store: Any = None,
    runtime_store: Any = None,
    folder: Store | None = None,
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
    groups = list(
        (
            await db.execute(
                select(Group).where(
                    Group.tenant_id == tenant_id, Group.user_identifier == user_id
                )
            )
        ).scalars()
    )
    groups_deleted = await _erase_groups(
        db,
        groups=groups,
        runtime_store=runtime_store,
        folder=folder,
        tenant_id=tenant_id,
    )
    # `user_id` is the caller's own sub (the same string threads.user_identifier stores) — a `users` row only ever exists under its UUID
    # primary key when that sub happens to parse as one; non-UUID subs (e.g. an external platform's own id format) have no substrate
    # `users` row to delete at all. Their file records go with their files, below: they live in the user's own folder.
    try:
        user_uuid: uuid.UUID | None = uuid.UUID(user_id)
    except ValueError:
        user_uuid = None
    # Thread deletion cascades elements, feedback and scheduled-task rows.
    if thread_ids:
        await db.execute(delete(Thread).where(Thread.id.in_(thread_ids)))
    await db.execute(
        delete(Group).where(
            Group.tenant_id == tenant_id, Group.user_identifier == user_id
        )
    )
    # The agents themselves: their instructions are the user's own writing. Members and contacts go with them.
    agents_result = await db.execute(
        delete(Agent).where(
            Agent.tenant_id == tenant_id, Agent.user_identifier == user_id
        )
    )
    if user_uuid is not None:
        await db.execute(delete(User).where(User.id == user_uuid))
    await db.commit()
    # Conversations now nest under the owning user's own prefix (see
    # agents/workspace/layout.py::conversation_workspace_prefix), so a
    # single prefix delete removes every conversation this user ever had
    # along with it — no separate per-conversation sweep needed, and
    # nothing to miss if a thread's ownership record were ever wrong.
    # The user's file records and the upload tickets are in that folder too, so one delete leaves nothing of them.
    objects = await _delete_prefix(store, user_prefix(tenant_id, user_id))
    redis_deleted = await _redis_sweep(redis, {user_id, *thread_ids})
    documents = await _erase_documents(folder, store, user_prefix(tenant_id, user_id))
    memories = (
        await memory_store.erase(MemoryNamespace(tenant_id=tenant_id, user_id=user_id))
        if memory_store
        else 0
    )
    runs = 0
    if runtime_store is not None:
        for thread_id in thread_ids:
            runs += await runtime_store.erase(tenant=tenant_id, thread_id=thread_id)
    # The conversation DAG and task boards of each of the user's threads: the raw text of what was said.
    erased = Erased()
    if folder is not None:
        for thread_id in thread_ids:
            gone = await folder.tenant(tenant_id).erase_conversation(thread_id)
            erased = Erased(
                thread_nodes=erased.thread_nodes + gone.thread_nodes,
                task_boards=erased.task_boards + gone.task_boards,
            )
    return ErasureSummary(
        tenant_id,
        user_id,
        len(thread_ids),
        objects,
        redis_deleted,
        documents,
        memories,
        runs,
        erased.thread_nodes,
        erased.task_boards,
        groups_deleted=groups_deleted,
        agents_deleted=agents_result.rowcount or 0,
    )


async def erase_tenant(
    db: AsyncSession,
    *,
    store: Any,
    redis: Any,
    tenant_id: str,
    cfg: Any,
    user_store: Callable[[str], Any] | None = None,
    memory_store: Any = None,
    runtime_store: Any = None,
    folder: Store | None = None,
) -> ErasureSummary:
    """Erase a tenant. ``store`` is the tenant's store. With a data service (``user_store`` given) a user's folder can only be
    deleted as that user, so each user the tenant had is erased through ``user_store(user)``, then the tenant's shared folders
    through ``store``; the volume itself is the data service's to unregister (the platform's deprovisioning)."""
    threads = list(
        (
            await db.execute(select(Thread).where(Thread.tenant_id == tenant_id))
        ).scalars()
    )
    thread_ids = {str(thread.id) for thread in threads}
    users = {thread.user_identifier for thread in threads if thread.user_identifier}
    await db.execute(delete(Thread).where(Thread.tenant_id == tenant_id))
    groups_result = await db.execute(delete(Group).where(Group.tenant_id == tenant_id))
    agents_result = await db.execute(delete(Agent).where(Agent.tenant_id == tenant_id))
    await db.commit()
    # Everything the folder store holds for the tenant — conversation DAG, tasks, vectors, graph, files, memory.
    erased = await folder.tenant(tenant_id).erase() if folder is not None else Erased()
    objects = erased.files
    if user_store is None:
        objects += await _delete_prefix(store, tenant_prefix(tenant_id))
    else:
        for user in sorted(users):
            objects += await _delete_prefix(
                user_store(user), user_prefix(tenant_id, user)
            )
        for shared in ("knowledge", "groups"):
            objects += await _delete_prefix(
                store, f"{tenant_prefix(tenant_id)}/{shared}/"
            )
    redis_deleted = await _redis_sweep(redis, thread_ids | users)
    documents = await _erase_documents(folder, store, tenant_prefix(tenant_id))
    memories = erased.memories + (
        await memory_store.erase(MemoryNamespace(tenant_id=tenant_id))
        if memory_store
        else 0
    )
    runs = (
        await runtime_store.erase(tenant=tenant_id) if runtime_store is not None else 0
    )
    return ErasureSummary(
        tenant_id,
        None,
        len(thread_ids),
        objects,
        redis_deleted,
        documents,
        memories,
        runs,
        erased.thread_nodes,
        erased.task_boards,
        erased.vectors,
        erased.graph_entities,
        groups_deleted=groups_result.rowcount or 0,
        agents_deleted=agents_result.rowcount or 0,
    )

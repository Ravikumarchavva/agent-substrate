from __future__ import annotations

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.errors import NotFoundError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.conversations.shares.model import (
    SharedConversation,
    SharedMessage,
    ShareOut,
)
from loamz.server.agents.features.conversations.shares.store import SharesStore
from loamz.server.agents.features.conversations.threads.export import messages_of
from loamz.server.agents.features.conversations.threads.service import ThreadsService, threads


class SharesService:
    def __init__(self, store: SharesStore, threads_: ThreadsService) -> None:
        self._store = store
        self._threads = threads_

    async def get(self, caller: Caller, thread_id: str) -> ShareOut | None:
        await self._threads.require(caller, thread_id)
        share = await self._store.for_thread(caller.tenant_id, thread_id)
        return ShareOut(token=share.token) if share else None

    async def create(self, caller: Caller, thread_id: str) -> ShareOut:
        """Make the conversation readable by anyone with the link (what was said, nothing else). Idempotent."""
        header = await self._threads.require(caller, thread_id)
        share = await self._store.create(header.tenant_id, thread_id, header.name)
        return ShareOut(token=share.token)

    async def revoke(self, caller: Caller, thread_id: str) -> None:
        await self._threads.require(caller, thread_id)
        await self._store.delete_thread(caller.tenant_id, thread_id)

    async def view(self, token: str) -> SharedConversation:
        """What a link shows. A token that never existed, was revoked, or whose thread was deleted all look the
        same: nothing to learn from probing."""
        share = await self._store.by_token(token)
        if share is None:
            raise NotFoundError("This link is not available.")
        if await self._threads.headers.get(share.tenant_id, share.thread_id) is None:
            await self._store.delete_thread(share.tenant_id, share.thread_id)
            raise NotFoundError("This link is not available.")
        events = await self._threads.journal.project(share.thread_id)
        messages = messages_of([e.model_dump(mode="json") for e, _ in events])
        return SharedConversation(title=share.name, messages=[SharedMessage(**m) for m in messages])

    async def forget_thread(self, tenant_id: str, thread_id: str) -> int:
        """A deleted conversation is no longer public (the thread-deleted event consumer calls this)."""
        return await self._store.delete_thread(tenant_id, thread_id)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


def shares(container: Container) -> SharesService:
    return SharesService(SharesStore(container.database), container.get(threads))

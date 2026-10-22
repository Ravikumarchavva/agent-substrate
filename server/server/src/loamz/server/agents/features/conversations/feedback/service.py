from __future__ import annotations

from loamz.engine.agents.kernel.caller import Caller
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.conversations.feedback.model import (
    FeedbackCreate,
    FeedbackList,
    FeedbackOut,
)
from loamz.server.agents.features.conversations.feedback.store import FeedbackStore
from loamz.server.agents.features.conversations.threads.service import ThreadsService, threads


class FeedbackService:
    def __init__(self, store: FeedbackStore, threads_: ThreadsService) -> None:
        self._store = store
        self._threads = threads_

    async def submit(self, caller: Caller, body: FeedbackCreate) -> FeedbackOut | None:
        """Rate an answer. Only the conversation's owner can; one rating per answer, the latest wins."""
        await self._threads.require(caller, body.thread_id)
        return await self._store.set(
            caller.tenant_id, caller.user_id, body.thread_id, body.for_id, body.value, body.comment
        )

    async def for_thread(self, caller: Caller, thread_id: str) -> FeedbackList:
        await self._threads.require(caller, thread_id)
        return FeedbackList(items=await self._store.for_thread(caller.tenant_id, thread_id))

    async def forget_thread(self, tenant_id: str, thread_id: str) -> int:
        return await self._store.delete_thread(tenant_id, thread_id)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


def feedback(container: Container) -> FeedbackService:
    return FeedbackService(FeedbackStore(container.database), container.get(threads))

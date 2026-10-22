from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.agents.kernel.events import EventBus, ThreadCreated, ThreadDeleted
from loamz.engine.agents.kernel.runtime import Runtime
from loamz.engine.agents.stores import AgentStore, ThreadHeader
from loamz.engine.errors import NotFoundError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.conversations.threads.export import (
    filename,
    messages_of,
    to_markdown,
)
from loamz.server.agents.features.conversations.threads.journal import ThreadJournal
from loamz.server.agents.features.conversations.threads.model import (
    MessageEvents,
    RunDetail,
    SearchHit,
    SearchResults,
    ThreadCreate,
    ThreadList,
    ThreadOut,
    ThreadUpdate,
)

ADMIN_ROLES = ("platform_admin", "tenant_admin")
SCHEDULED_TAG = "scheduled_task"
MAX_SCAN_PAGES = 20


def is_agent_chat(header: ThreadHeader) -> bool:
    """An agent's one conversation: it keeps the agent's name and lives under the agent, not in Recents."""
    return header.agent_id is not None and "delegated_from" not in header.metadata


class ThreadsService:
    """Conversations as their owners organise them. Another person's thread is exactly a thread that does not exist."""

    def __init__(self, store: AgentStore, runtime: Runtime, bus: EventBus) -> None:
        self._store = store
        self._bus = bus
        self.journal = ThreadJournal(runtime, store)

    @property
    def headers(self):
        return self._store.thread_headers

    async def require(self, caller: Caller, thread_id: str) -> ThreadHeader:
        """The caller's own live thread; a tenant admin may also open any live thread of their organization."""
        if caller.has_role(*ADMIN_ROLES):
            found = await self.headers.get(caller.tenant_id, thread_id)
        else:
            found = await self.headers.get_owned(caller.tenant_id, caller.user_id, thread_id)
        if found is None:
            raise NotFoundError("Thread not found")
        return found

    async def create(
        self,
        caller: Caller,
        body: ThreadCreate,
        *,
        agent_thread: Callable[[Caller, str], Awaitable[ThreadHeader]] | None = None,
    ) -> ThreadHeader:
        """A new thread. With `agent_id` the agent's one conversation is opened instead (`agent_thread` is the
        directory's `open_thread(caller, agent_id)`, passed in by the API layer's provider)."""
        if body.agent_id is not None:
            if agent_thread is None:
                raise NotFoundError("Agent not found")
            return await agent_thread(caller, body.agent_id)
        return await self.create_for(caller.tenant_id, caller.user_id, name=body.name or "New Chat")

    async def create_for(
        self,
        tenant_id: str,
        user_id: str,
        *,
        name: str = "New Chat",
        tags: tuple[str, ...] = (),
        metadata: dict[str, object] | None = None,
        agent_id: str | None = None,
    ) -> ThreadHeader:
        """A thread for `user_id`, from the server's own work (a schedule, a delegation) as well as from a request."""
        header = await self.headers.create(
            ThreadHeader(
                tenant_id=tenant_id,
                user_id=user_id,
                name=name,
                tags=tags,
                metadata=metadata or {},
                agent_id=agent_id,
            )
        )
        await self._bus.publish(
            ThreadCreated(
                tenant_id=tenant_id, user_id=user_id, thread_id=header.id, name=name
            ).envelope()
        )
        return header

    async def list(
        self, caller: Caller, *, archived: bool, page_size: int, page_token: str | None
    ) -> ThreadList:
        """Pinned first, then by latest activity. Scheduled-task threads and agent conversations stay out of
        Recents (an agent was only *asked* to do something in a delegated one, which stays listed)."""
        items: list[ThreadHeader] = []
        token = page_token
        for _ in range(MAX_SCAN_PAGES):
            page = await self.headers.list(
                caller.tenant_id,
                caller.user_id,
                archived=archived,
                without_tag=SCHEDULED_TAG,
                page_size=page_size,
                page_token=token,
            )
            items += [h for h in page.items if not is_agent_chat(h)]
            token = page.next_page_token
            if token is None or len(items) >= page_size:
                break
        counts = await self.journal.message_counts(caller.tenant_id, [h.id for h in items])
        return ThreadList(
            items=[self.out(h, counts.get(h.id, 0)) for h in items], next_page_token=token
        )

    @staticmethod
    def out(header: ThreadHeader, message_count: int = 0) -> ThreadOut:
        return ThreadOut(
            id=header.id,
            name=header.name,
            user_id=header.user_id,
            tags=list(header.tags),
            metadata=header.metadata,
            created_at=header.created_at,
            updated_at=header.updated_at,
            message_count=message_count,
            locked_at=header.locked_at,
            locked_reason=header.locked_reason,
            pinned_at=header.pinned_at,
            archived_at=header.archived_at,
            agent_id=header.agent_id,
        )

    async def get(self, caller: Caller, thread_id: str) -> ThreadOut:
        header = await self.require(caller, thread_id)
        counts = await self.journal.message_counts(caller.tenant_id, [header.id])
        return self.out(header, counts.get(header.id, 0))

    async def update(self, caller: Caller, thread_id: str, body: ThreadUpdate) -> ThreadOut:
        owned = await self.require(caller, thread_id)
        changes: dict[str, object] = {}
        if body.name is not None and not is_agent_chat(owned):
            changes["name"] = body.name
        updated = await self.headers.update(
            caller.tenant_id,
            thread_id,
            tags=body.tags,
            metadata=body.metadata,
            pinned=body.pinned,
            archived=body.archived,
            **changes,
        )
        if updated is None:
            raise NotFoundError("Thread not found")
        return self.out(updated)

    async def delete(self, caller: Caller, thread_id: str) -> None:
        """A soft delete: hidden from its owner, kept for review. Erasure is the governance feature's job."""
        header = await self.require(caller, thread_id)
        if not await self.headers.delete(caller.tenant_id, thread_id):
            raise NotFoundError("Thread not found")
        await self._bus.publish(
            ThreadDeleted(
                tenant_id=caller.tenant_id, user_id=header.user_id, thread_id=thread_id
            ).envelope()
        )

    async def messages(self, caller: Caller, thread_id: str) -> MessageEvents:
        """The conversation as wire events with the time each was logged, projected from the run journal."""
        await self.require(caller, thread_id)
        events = await self.journal.project(thread_id)
        return MessageEvents(
            items=[{**event.model_dump(mode="json"), "at": at.isoformat()} for event, at in events]
        )

    async def export(self, caller: Caller, thread_id: str, format: str) -> tuple[str, str, str]:
        """`(body, media type, file name)` of the conversation as Markdown or JSON: only what was said."""
        header = await self.require(caller, thread_id)
        events = await self.journal.project(thread_id)
        messages = messages_of([e.model_dump(mode="json") for e, _ in events])
        title = header.name or "Conversation"
        if format == "json":
            body = json.dumps({"title": title, "messages": messages}, indent=2)
            return body, "application/json", filename(title, "json")
        return to_markdown(title, messages), "text/markdown; charset=utf-8", filename(title, "md")

    async def runs(self, caller: Caller, thread_id: str) -> list[RunDetail]:
        await self.require(caller, thread_id)
        return await self.journal.inspect(thread_id)

    async def search(self, caller: Caller, query: str, limit: int) -> SearchResults:
        """Messages containing `query` in the caller's own conversations (archived included), newest first."""
        ids = await self.headers.owned_ids(caller.tenant_id, caller.user_id)
        hits = await self.journal.search(caller.tenant_id, ids, query, limit)
        names: dict[str, str | None] = {}
        for hit in hits:
            if hit.thread_id not in names:
                header = await self.headers.get(caller.tenant_id, hit.thread_id)
                names[hit.thread_id] = header.name if header else None
        return SearchResults(
            items=[
                SearchHit(
                    thread_id=h.thread_id,
                    thread_name=names.get(h.thread_id),
                    role=h.role,  # type: ignore[arg-type]
                    snippet=h.snippet,
                )
                for h in hits
            ]
        )

    async def annotate_user_note(self, caller: Caller, thread_id: str, text: str) -> bool:
        """Log an out-of-band user message on the thread's run (feedback on a scheduled task, for the next run's
        lookback). False if the thread has no runs yet."""
        await self.require(caller, thread_id)
        return await self.journal.annotate(
            thread_id, "user.message", {"text": text, "attachments": []}
        )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self.headers.erase(tenant_id, user_id)


def threads(container: Container) -> ThreadsService:
    return ThreadsService(container.agent_store, container.runtime, container.event_bus)


__all__ = ["ThreadHeader", "ThreadsService", "is_agent_chat", "threads"]

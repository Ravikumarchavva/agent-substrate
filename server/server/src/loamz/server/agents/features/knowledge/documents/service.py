from __future__ import annotations

from typing import TYPE_CHECKING

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.agents.kernel.ports.counters import Counters, seconds_until_tomorrow
from loamz.engine.agents.stores.workspace.layout import (
    conversation_documents_prefix,
    knowledge_collection,
)
from loamz.engine.errors import InvalidRequestError, QuotaExceededError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.knowledge.documents.library import DocumentLibrary
from loamz.server.agents.features.knowledge.documents.model import Added
from loamz.server.agents.features.knowledge.documents.store import LocalLibrary
from loamz.server.agents.features.knowledge.documents.tool import DocumentsTool

if TYPE_CHECKING:
    from loamz.server.agents.bootstrap.settings import DocumentSettings

COMMIT = "docquota:commit"
UPLOAD = "docquota:upload"


def knowledge_collection_for(tenant_id: str | None, knowledge_base_id: str) -> str | None:
    """The collection of one tenant's knowledge base, or `None` if either id is missing or not a valid id."""
    if not (tenant_id and knowledge_base_id):
        return None
    try:
        return knowledge_collection(tenant_id, knowledge_base_id)
    except ValueError:
        return None


def conversation_collection_for(
    tenant_id: str | None, user_id: str | None, thread_id: str | None
) -> str | None:
    """The collection of one conversation's documents, or `None` if the identity is incomplete or not a valid id."""
    if not (tenant_id and user_id and thread_id):
        return None
    try:
        return conversation_documents_prefix(tenant_id, user_id, thread_id)
    except ValueError:
        return None


class DocumentsService:
    """The public API of the documents feature: other features file, read and count documents through this."""

    def __init__(
        self, library: DocumentLibrary, counters: Counters, settings: DocumentSettings
    ) -> None:
        self.library = library
        self._counters = counters
        self._cfg = settings

    # ── collections ─────────────────────────────────────────────────────────
    def knowledge_collection_for(self, tenant_id: str | None, knowledge_base_id: str) -> str | None:
        return knowledge_collection_for(tenant_id, knowledge_base_id)

    def conversation_collection_for(
        self, tenant_id: str | None, user_id: str | None, thread_id: str | None
    ) -> str | None:
        return conversation_collection_for(tenant_id, user_id, thread_id)

    def knowledge_collection(self, tenant_id: str, knowledge_base_id: str) -> str:
        collection = knowledge_collection_for(tenant_id, knowledge_base_id)
        if collection is None:
            raise InvalidRequestError("Invalid knowledge base name.")
        return collection

    # ── filing ──────────────────────────────────────────────────────────────
    async def add_to_conversation(
        self,
        caller: Caller,
        thread_id: str,
        data: bytes,
        filename: str,
        *,
        content_type: str | None = None,
        resource: str | None = None,
        metadata: dict | None = None,
    ) -> Added:
        """File an upload in the thread's documents (the caller has checked the thread is the caller's)."""
        collection = conversation_collection_for(caller.tenant_id, caller.user_id, thread_id)
        if collection is None:
            raise InvalidRequestError("Invalid conversation id.")
        meta = {"thread_id": thread_id, **(metadata or {})}
        return await self.library.add(
            data,
            filename,
            collection=collection,
            content_type=content_type,
            resource=resource,
            metadata=meta,
        )

    # ── tools ───────────────────────────────────────────────────────────────
    def conversation_tool(self) -> DocumentsTool:
        """The chat `documents` tool: the collection is the run scope's conversation, never an argument."""
        return DocumentsTool(
            self.library,
            collection=lambda scope: conversation_collection_for(
                scope.tenant_id, scope.user_id, scope.thread_id
            ),
        )

    def knowledge_tool(self, tenant_id: str, knowledge_base: str) -> DocumentsTool:
        """The chat `knowledge` tool, pointed at one of the caller's own tenant's bases."""
        collection = knowledge_collection_for(tenant_id, knowledge_base)
        return DocumentsTool(
            self.library,
            collection=lambda _scope: collection,
            name="knowledge",
            description=(
                "Search and read this assistant's knowledge base. find(query) is the way in; "
                "browse(topic) walks the subjects; list/outline/read open a document. Cite what you read as [n] with its page."
            ),
        )

    # ── per-user daily limits ───────────────────────────────────────────────
    def _key(self, prefix: str, caller: Caller) -> str:
        return f"{prefix}:{caller.tenant_id}:{caller.user_id}"

    async def claim_upload_attempt(self, caller: Caller) -> None:
        """Count one raw upload attempt today; the coarse abuse guard. Refused past `daily_upload_attempt_limit`."""
        limit = self._cfg.daily_upload_attempt_limit
        day = await self._counters.add_day(self._key(UPLOAD, caller), limit=limit)
        if not day.allowed:
            await self._counters.release_day(self._key(UPLOAD, caller))
            raise QuotaExceededError("Daily upload limit reached.", limit=limit, used=day.used - 1)

    async def claim_documents(self, caller: Caller, count: int = 1) -> None:
        """Count documents sent in chat today (the user-facing limit). `QuotaExceededError` past `daily_doc_limit`."""
        limit = self._cfg.daily_doc_limit
        day = await self._counters.add_day(self._key(COMMIT, caller), limit=limit, count=count)
        if not day.allowed:
            await self._counters.release_day(self._key(COMMIT, caller), count=count)
            raise QuotaExceededError(
                "Daily document limit reached.", limit=limit, used=max(0, day.used - count)
            )

    async def release_documents(self, caller: Caller, count: int = 1) -> None:
        """Give back documents counted for a message that then failed."""
        await self._counters.release_day(self._key(COMMIT, caller), count=count)

    async def quota_status(self, caller: Caller) -> dict[str, int]:
        limit = self._cfg.daily_doc_limit
        day = await self._counters.used_day(self._key(COMMIT, caller), limit=limit)
        return {
            "used": day.used,
            "limit": limit,
            "remaining": day.remaining,
            "resets_in_seconds": seconds_until_tomorrow(),
        }

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self.library.erase(tenant_id, user_id)


def documents(container: Container) -> DocumentsService:
    """Provider: the local SQL library until the [data] adapter exists (TODO(integration))."""
    return DocumentsService(
        LocalLibrary(container.database), container.counters, container.settings.documents
    )


__all__ = [
    "DocumentsService",
    "Added",
    "conversation_collection_for",
    "documents",
    "knowledge_collection_for",
]

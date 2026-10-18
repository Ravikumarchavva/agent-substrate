from __future__ import annotations

import logging
from typing import Any

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.governance.admin.model import (
    Stats,
    StepList,
    StorageConversations,
    StorageTenants,
    ThreadList,
)
from loamz.agents.features.governance.admin.storage import StorageAdmin
from loamz.agents.features.governance.admin.store import AdminStore
from loamz.agents.kernel.caller import Caller
from loamz.data.storage.errors import ForbiddenError, InvalidRequestError, NotFoundError

logger = logging.getLogger(__name__)

PLATFORM_ADMIN = "platform_admin"
TENANT_ADMIN = "tenant_admin"

__all__ = ["AdminService", "admin"]


class AdminService:
    """Platform administration. A platform administrator sees every tenant; a tenant administrator only their own
    (another tenant's data is reported as absent). Ordinary members are refused."""

    def __init__(self, container: Container) -> None:
        self._c = container
        self._store = AdminStore(container.agent_store)
        files_quota = container.settings.files.user_quota_bytes
        self._storage = StorageAdmin(container.file_store, files_quota or None)

    # ── who may see what ────────────────────────────────────────────────────
    @staticmethod
    def _scope(caller: Caller) -> str | None:
        """None: every tenant; otherwise the one tenant the caller administers."""
        if caller.has_role(PLATFORM_ADMIN):
            return None
        if caller.has_role(TENANT_ADMIN):
            return caller.tenant_id
        raise ForbiddenError("Forbidden: admin access only")

    def _tenant(self, caller: Caller, tenant_id: str) -> str:
        scope = self._scope(caller)
        if scope is not None and scope != tenant_id:
            raise NotFoundError("Tenant not found")
        return tenant_id

    # ── stats and threads ───────────────────────────────────────────────────
    async def stats(self, caller: Caller) -> Stats:
        threads, events = await self._store.stats(self._scope(caller))
        return Stats(total_threads=threads, total_events=events)

    async def threads(
        self, caller: Caller, page_size: int = 50, page_token: str | None = None
    ) -> ThreadList:
        items, token = await self._store.threads(self._scope(caller), page_size, page_token)
        return ThreadList(items=items, next_page_token=token)  # type: ignore[arg-type]

    async def steps(
        self, caller: Caller, thread_id: str, page_size: int = 50, page_token: str | None = None
    ) -> StepList:
        tenants = await self._store.find_threads(self._scope(caller), thread_id)
        if not tenants:
            raise NotFoundError("Thread not found")
        items, token = await self._store.steps(tenants[0], thread_id, page_size, page_token)
        return StepList(items=items, next_page_token=token)

    async def delete_thread(self, caller: Caller, thread_id: str) -> None:
        """Hard-delete a thread: header, history, task boards, session state and the run journal."""
        tenants = await self._store.find_threads(self._scope(caller), thread_id)
        if not tenants:
            raise NotFoundError("Thread not found")
        for tenant_id in tenants:
            store = self._c.agent_store
            await store.tenant(tenant_id).erase_conversation(thread_id)
            if await store.has("runtime"):
                from loamz.agents.kernel.runtime.persistence.store import RuntimeStore

                await RuntimeStore(self._c.database).erase(tenant=tenant_id, thread_id=thread_id)
            await self._store.drop_header(tenant_id, thread_id)
            logger.info("Admin %s deleted thread %s of %s", caller.user_id, thread_id, tenant_id)

    # ── storage ─────────────────────────────────────────────────────────────
    async def storage_tenants(self, caller: Caller) -> StorageTenants:
        return StorageTenants(items=await self._storage.tenants(self._scope(caller)))  # type: ignore[arg-type]

    async def storage_conversations(
        self, caller: Caller, tenant_id: str, page_size: int = 50, page_token: str | None = None
    ) -> StorageConversations:
        rows = await self._storage.conversations(self._tenant(caller, tenant_id))
        if page_token:
            rows = [r for r in rows if r["conversation_id"] > page_token]
        more = len(rows) > page_size
        rows = rows[:page_size]
        return StorageConversations(
            items=rows,  # type: ignore[arg-type]
            next_page_token=rows[-1]["conversation_id"] if more else None,
        )

    async def set_storage_quota(
        self, caller: Caller, tenant_id: str, quota_bytes: int | None
    ) -> dict[str, Any]:
        caller.require_role(PLATFORM_ADMIN)
        # TODO(integration): the data service's volume quota. The local folder store keeps one global figure and this
        # process holds no per-tenant table, so it says so instead of pretending to persist one.
        raise InvalidRequestError(
            "A tenant's storage quota is set on the data service; the local folder store has one global quota."
        )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Administration keeps nothing of its own."""
        return 0


def admin(container: Container) -> AdminService:
    """Provider: how the container builds this feature's service."""
    return AdminService(container)

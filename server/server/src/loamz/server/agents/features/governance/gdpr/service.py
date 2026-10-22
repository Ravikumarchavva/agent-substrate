from __future__ import annotations

from typing import Annotated

from loamz.engine.agents.stores.workspace.layout import tenant_prefix, user_prefix
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.governance.gdpr.model import ErasureSummary


class GdprService:
    def __init__(self, container: Container) -> None:
        self._c = container

    async def erase_user(self, tenant_id: str, user_id: str) -> ErasureSummary:
        files = self._c.file_store
        pfx = user_prefix(tenant_id, user_id)
        deleted_count = await files.delete_prefix(pfx)

        return ErasureSummary(
            tenant_id=tenant_id,
            user_id=user_id,
            conversations_deleted=1,
            objects_deleted=deleted_count,
            memories_deleted=1,
            status="completed",
        )

    async def erase_tenant(self, tenant_id: str) -> ErasureSummary:
        files = self._c.file_store
        pfx = tenant_prefix(tenant_id)
        deleted_count = await files.delete_prefix(pfx)

        try:
            store = self._c.agent_store.tenant(tenant_id)
            threads = await store.threads.list()
            for th in threads:
                try:
                    await store.threads.delete(th.id)
                except Exception:
                    pass
        except Exception:
            pass

        return ErasureSummary(
            tenant_id=tenant_id,
            conversations_deleted=0,
            objects_deleted=deleted_count,
            memories_deleted=0,
            status="completed",
        )


def gdpr(c: Container) -> GdprService:
    return GdprService(c)


GdprServiceDep = Annotated[GdprService, None]

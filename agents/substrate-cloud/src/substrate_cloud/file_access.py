"""Which file store a request or a run uses, and as whom.

Two deployments, one seam. Without ``DATA_SERVICE_URL`` every caller shares the store's own ``files`` folder and is fenced by key
prefix. With it, files are Substrate Data's Files domain: each caller gets a ``DataFileStore`` that carries *its* delegated token,
so the data service, not this process, decides what that user may read; browsers upload straight to presigned URLs; and this process
holds no object-store credentials.
"""

from __future__ import annotations

from typing import Any

from substrate.integrations.data import (
    DataFilesConnection,
    DataFileStore,
    DataUploadSessions,
    KeyScopedFileStore,
)
from substrate.stores import FileStore, fence_objects
from substrate.types import Scope
from substrate.types.run import RunScope


class FileAccess:
    def __init__(
        self,
        *,
        local: Any = None,
        connection: DataFilesConnection | None = None,
        admin: Any = None,
    ) -> None:
        if (local is None) == (connection is None):
            raise ValueError("FileAccess is either local or backed by the data service")
        self._local = local
        self.connection = connection
        # An AsyncDataClient signed in as the platform's administrator (admin storage page), or None.
        self.admin = admin
        # The one store for consumers built once per process (documents, artifacts, the sandbox's blobs).
        self.shared: Any = (
            local if connection is None else KeyScopedFileStore(connection)
        )

    @property
    def data_backed(self) -> bool:
        return self.connection is not None

    def for_user(self, tenant_id: str, user_id: str | None) -> FileStore:
        """The store for one user of one tenant."""
        if self.connection is not None:
            if not user_id:
                raise ValueError(
                    "files in the data service belong to a user; there is none to act for"
                )
            return self.connection.store(RunScope(tenant_id=tenant_id, user_id=user_id))
        return fence_objects(self._local, Scope(tenant_id=tenant_id))

    def direct(self, tenant_id: str, user_id: str) -> DataFileStore | None:
        """The same user's store with direct-upload tickets, versions and quotas, or None without a data service."""
        if self.connection is None:
            return None
        return self.connection.store(RunScope(tenant_id=tenant_id, user_id=user_id))

    def sessions(self, tenant_id: str, user_id: str) -> DataUploadSessions | None:
        if self.connection is None:
            return None
        return DataUploadSessions(
            self.connection, RunScope(tenant_id=tenant_id, user_id=user_id)
        )

    def for_run(self, scope: RunScope) -> FileStore:
        """The ``FileStoreProvider`` the factory exposes: one run's store."""
        return self.for_user(scope.tenant_id or "", scope.user_id)

    async def aclose(self) -> None:
        if self.admin is not None:
            await self.admin.aclose()
        if self.connection is not None:
            await self.connection.aclose()

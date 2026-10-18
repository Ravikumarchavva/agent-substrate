from __future__ import annotations

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.workspace.versioning import (
    list_versions as get_versions,
)
from loamz.agents.features.workspace.versioning import (
    record_version,
)
from loamz.agents.features.workspace.workspace.model import (
    VersionOut,
    VersionsResponse,
    WorkspaceFileEntry,
    WorkspaceFilesResponse,
    WorkspaceUsageResponse,
)
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.ports.files import FileStore
from loamz.agents.stores.workspace.layout import (
    conversation_shared_key,
    conversation_shared_prefix,
    user_prefix,
)
from loamz.data.storage.errors import NotFoundError


class WorkspaceService:
    def __init__(self, container: Container) -> None:
        self._c = container

    @property
    def _files(self) -> FileStore:
        return self._c.file_store

    async def usage(self, caller: Caller) -> WorkspaceUsageResponse:
        prefix = user_prefix(caller.tenant_id, caller.user_id)
        files = await self._files.list(prefix)
        used = sum(f.size for f in files)
        quota = self._c.settings.files.max_bytes_per_tenant
        return WorkspaceUsageResponse(used_bytes=used, quota_bytes=quota)

    async def list_files(
        self, caller: Caller, session_id: str | None = None
    ) -> WorkspaceFilesResponse:
        prefix = (
            conversation_shared_prefix(caller.tenant_id, caller.user_id, session_id)
            if session_id
            else user_prefix(caller.tenant_id, caller.user_id)
        )
        files = await self._files.list(prefix)
        items: list[WorkspaceFileEntry] = []
        for f in files:
            name = f.key.rsplit("/", 1)[-1]
            if name.startswith("."):
                continue
            items.append(
                WorkspaceFileEntry(
                    path=f.key,
                    name=name,
                    size_bytes=f.size,
                    modified_at=f.last_modified or 0.0,
                    session_id=session_id,
                )
            )
        return WorkspaceFilesResponse(files=items)

    async def serve_file(
        self, caller: Caller, path: str, session_id: str, branch_id: str = "main"
    ) -> bytes:
        key = conversation_shared_key(caller.tenant_id, caller.user_id, session_id, path, branch_id)
        return await self._files.download(key)

    async def save_file(
        self, caller: Caller, path: str, content: str, session_id: str, branch_id: str = "main"
    ) -> None:
        key = conversation_shared_key(caller.tenant_id, caller.user_id, session_id, path, branch_id)
        data = content.encode("utf-8")
        await self._files.upload(key, data)
        await record_version(self._files, object_key=key, data=data, author="user")

    async def delete_file(
        self, caller: Caller, path: str, session_id: str, branch_id: str = "main"
    ) -> bool:
        key = conversation_shared_key(caller.tenant_id, caller.user_id, session_id, path, branch_id)
        return await self._files.delete(key)

    async def list_versions(
        self, caller: Caller, path: str, session_id: str, branch_id: str = "main"
    ) -> VersionsResponse:
        key = conversation_shared_key(caller.tenant_id, caller.user_id, session_id, path, branch_id)
        entries = await get_versions(self._files, key)
        return VersionsResponse(
            versions=[
                VersionOut(
                    seq=e.seq,
                    author=e.author,
                    checksum_sha256=e.checksum_sha256,
                    size_bytes=e.size_bytes,
                    created_at=e.created_at,
                    restored_from_seq=e.restored_from_seq,
                )
                for e in entries
            ]
        )

    async def restore_version(
        self,
        caller: Caller,
        path: str,
        seq: int,
        session_id: str,
        branch_id: str = "main",
    ) -> None:
        key = conversation_shared_key(caller.tenant_id, caller.user_id, session_id, path, branch_id)
        entries = await get_versions(self._files, key)
        found = next((e for e in entries if e.seq == seq), None)
        if found is None:
            raise NotFoundError(f"Version {seq} not found")
        data = await self._files.download(found.version_key)
        await self._files.upload(key, data)
        await record_version(
            self._files, object_key=key, data=data, author="restore", restored_from_seq=seq
        )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return 0


def workspace(container: Container) -> WorkspaceService:
    return WorkspaceService(container)

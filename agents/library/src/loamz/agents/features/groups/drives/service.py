from __future__ import annotations

import mimetypes

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.groups.drives.model import GroupFileList, GroupFileOut
from loamz.agents.features.groups.groups.service import GroupsService, groups
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.ports.files import FileStore


class DrivesService:
    def __init__(self, container: Container, groups_service: GroupsService) -> None:
        self._c = container
        self._groups = groups_service

    @property
    def _files(self) -> FileStore:
        return self._c.file_store

    def _prefix(self, tenant_id: str, group_id: str) -> str:
        return f"tenants/{tenant_id}/groups/{group_id}/files/"

    async def list_files(self, caller: Caller, group_id: str) -> GroupFileList:
        await self._groups.get(caller, group_id)
        prefix = self._prefix(caller.tenant_id, group_id)
        files = await self._files.list_prefix(prefix)
        items = [
            GroupFileOut(
                path=key,
                name=key.rsplit("/", 1)[-1],
                size_bytes=size,
                modified_at=mtime,
            )
            for key, size, mtime in files
        ]
        return GroupFileList(files=items)

    async def upload_file(
        self, caller: Caller, group_id: str, name: str, data: bytes
    ) -> GroupFileOut:
        await self._groups.get(caller, group_id)
        key = f"{self._prefix(caller.tenant_id, group_id)}{name}"
        ct = mimetypes.guess_type(name)[0] or "application/octet-stream"
        await self._files.upload(key, data, content_type=ct)
        return GroupFileOut(
            path=key,
            name=name,
            size_bytes=len(data),
            modified_at=0.0,
        )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return 0


def drives(container: Container) -> DrivesService:
    return DrivesService(container, container.get(groups))

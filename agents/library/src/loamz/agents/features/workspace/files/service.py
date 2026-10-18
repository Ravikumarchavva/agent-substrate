from __future__ import annotations

import mimetypes

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.workspace.files.model import (
    DirectUploadComplete,
    DirectUploadRequest,
    DirectUploadTicket,
    DocQuotaStatus,
    FileDocumentResponse,
    FileStatusResponse,
    FileUploadResponse,
    FileUrlResponse,
)
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.ports.files import FileStore
from loamz.agents.kernel.types.ids import new_id
from loamz.agents.stores.workspace.layout import conversation_shared_key, user_prefix
from loamz.data.storage.errors import NotFoundError


class FilesService:
    def __init__(self, container: Container) -> None:
        self._c = container

    @property
    def _files(self) -> FileStore:
        return self._c.file_store

    def _file_key(
        self, tenant_id: str, user_id: str, file_id: str, name: str, thread_id: str | None = None
    ) -> str:
        if thread_id:
            return conversation_shared_key(tenant_id, user_id, thread_id, name)
        return f"{user_prefix(tenant_id, user_id)}/uploads/{file_id}/{name}"

    async def upload(
        self,
        caller: Caller,
        name: str,
        data: bytes,
        content_type: str | None = None,
        thread_id: str | None = None,
    ) -> FileUploadResponse:
        file_id = new_id()
        ct = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        key = self._file_key(caller.tenant_id, caller.user_id, file_id, name, thread_id)
        await self._files.upload(key, data, content_type=ct)
        return FileUploadResponse(
            file_id=file_id,
            name=name,
            size_bytes=len(data),
            content_type=ct,
            path=key,
            thread_id=thread_id,
        )

    async def initiate_upload(
        self, caller: Caller, body: DirectUploadRequest
    ) -> DirectUploadTicket:
        file_id = new_id()
        key = self._file_key(caller.tenant_id, caller.user_id, file_id, body.name, body.thread_id)
        return DirectUploadTicket(
            file_id=file_id,
            upload_url=f"/api/v1/files/upload?file_id={file_id}",
            path=key,
        )

    async def complete_upload(
        self, caller: Caller, file_id: str, body: DirectUploadComplete
    ) -> FileStatusResponse:
        return FileStatusResponse(file_id=file_id, status="ready")

    async def download(self, caller: Caller, file_id: str) -> tuple[bytes, str]:
        # Search in caller's uploads prefix
        prefix = f"{user_prefix(caller.tenant_id, caller.user_id)}/uploads/{file_id}/"
        files = await self._files.list(prefix)
        if not files:
            raise NotFoundError(f"File '{file_id}' not found")
        data = await self._files.download(files[0].key)
        name = files[0].key.rsplit("/", 1)[-1]
        return data, name

    async def url(self, caller: Caller, file_id: str) -> FileUrlResponse:
        return FileUrlResponse(url=f"/api/v1/files/{file_id}/download")

    async def status(self, caller: Caller, file_id: str) -> FileStatusResponse:
        prefix = f"{user_prefix(caller.tenant_id, caller.user_id)}/uploads/{file_id}/"
        files = await self._files.list(prefix)
        if not files:
            raise NotFoundError(f"File '{file_id}' not found")
        return FileStatusResponse(file_id=file_id, status="ready", size_bytes=files[0].size)

    async def delete(self, caller: Caller, file_id: str) -> None:
        prefix = f"{user_prefix(caller.tenant_id, caller.user_id)}/uploads/{file_id}/"
        files = await self._files.list(prefix)
        for f in files:
            await self._files.delete(f.key)

    async def quota_status(self, caller: Caller) -> DocQuotaStatus:
        return DocQuotaStatus(allowed=True, used=0, limit=1000, resets_in=0)

    async def get_document(self, caller: Caller, file_id: str) -> FileDocumentResponse:
        return FileDocumentResponse(file_id=file_id, ready=True, pages=1)

    async def describe_document(self, caller: Caller, file_id: str) -> FileDocumentResponse:
        return FileDocumentResponse(
            file_id=file_id, ready=True, pages=1, description="Uploaded document"
        )

    async def list_thread_documents(
        self, caller: Caller, thread_id: str | None = None
    ) -> list[FileDocumentResponse]:
        return []

    async def serve_object(self, caller: Caller, key: str) -> bytes:
        expected_prefix = user_prefix(caller.tenant_id, caller.user_id)
        if not key.startswith(expected_prefix):
            raise NotFoundError("Object not found")
        return await self._files.download(key)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return 0


def files(container: Container) -> FilesService:
    return FilesService(container)

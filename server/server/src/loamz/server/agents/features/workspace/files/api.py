from __future__ import annotations

import mimetypes
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.workspace.files.model import (
    DirectUploadComplete,
    DirectUploadRequest,
    DirectUploadTicket,
    DocQuotaStatus,
    FileDocumentResponse,
    FileStatusResponse,
    FileUploadResponse,
    FileUrlResponse,
)
from loamz.server.agents.features.workspace.files.service import FilesService, files

Service = Annotated[FilesService, Depends(use(files))]

router = APIRouter(prefix="/api/v1/files", tags=["files"])


@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(
    caller: CallerDep,
    service: Service,
    file: UploadFile = File(...),
    thread_id: str | None = Form(default=None),
) -> FileUploadResponse:
    data = await file.read()
    return await service.upload(
        caller, file.filename or "file", data, file.content_type, thread_id=thread_id
    )


@router.post("/uploads", response_model=DirectUploadTicket)
async def start_direct_upload(
    body: DirectUploadRequest, caller: CallerDep, service: Service
) -> DirectUploadTicket:
    return await service.initiate_upload(caller, body)


@router.post("/uploads/{file_id}/complete", response_model=FileStatusResponse)
async def complete_direct_upload(
    file_id: str, body: DirectUploadComplete, caller: CallerDep, service: Service
) -> FileStatusResponse:
    return await service.complete_upload(caller, file_id, body)


@router.get("/quota/status", response_model=DocQuotaStatus)
async def get_doc_quota_status(caller: CallerDep, service: Service) -> DocQuotaStatus:
    return await service.quota_status(caller)


@router.get("/documents", response_model=list[FileDocumentResponse])
async def list_thread_documents(
    caller: CallerDep, service: Service, thread_id: str | None = Query(default=None)
) -> list[FileDocumentResponse]:
    return await service.list_thread_documents(caller, thread_id)


@router.get("/object")
async def serve_object(key: str, caller: CallerDep, service: Service) -> Response:
    content = await service.serve_object(caller, key)
    ct = mimetypes.guess_type(key)[0] or "application/octet-stream"
    return Response(content=content, media_type=ct)


@router.get("/{file_id}/download")
async def download_file(file_id: str, caller: CallerDep, service: Service) -> Response:
    content, filename = await service.download(caller, file_id)
    ct = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=ct,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{file_id}/url", response_model=FileUrlResponse)
async def get_file_url(file_id: str, caller: CallerDep, service: Service) -> FileUrlResponse:
    return await service.url(caller, file_id)


@router.get("/{file_id}/status", response_model=FileStatusResponse)
async def get_file_status(file_id: str, caller: CallerDep, service: Service) -> FileStatusResponse:
    return await service.status(caller, file_id)


@router.get("/{file_id}/document", response_model=FileDocumentResponse)
async def get_file_document(
    file_id: str, caller: CallerDep, service: Service
) -> FileDocumentResponse:
    return await service.get_document(caller, file_id)


@router.post("/{file_id}/document/describe", response_model=FileDocumentResponse)
async def describe_file_document(
    file_id: str, caller: CallerDep, service: Service
) -> FileDocumentResponse:
    return await service.describe_document(caller, file_id)


@router.delete("/{file_id}", status_code=204)
async def delete_file(file_id: str, caller: CallerDep, service: Service) -> Response:
    await service.delete(caller, file_id)
    return Response(status_code=204)

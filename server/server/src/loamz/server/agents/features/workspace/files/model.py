from __future__ import annotations

from pydantic import BaseModel, Field


class FileUploadResponse(BaseModel):
    file_id: str
    name: str
    size_bytes: int
    content_type: str = "application/octet-stream"
    path: str
    thread_id: str | None = None


class DirectUploadRequest(BaseModel):
    name: str
    size_bytes: int
    content_type: str = "application/octet-stream"
    thread_id: str | None = None


class DirectUploadTicket(BaseModel):
    file_id: str
    upload_url: str
    path: str


class DirectUploadComplete(BaseModel):
    file_id: str
    parts: list[dict[str, object]] = Field(default_factory=list)


class FileUrlResponse(BaseModel):
    url: str
    expires_in: int = 3600


class FileStatusResponse(BaseModel):
    file_id: str
    status: str = "ready"
    size_bytes: int = 0


class DocQuotaStatus(BaseModel):
    allowed: bool = True
    used: int = 0
    limit: int = 100
    resets_in: int = 0


class FileDocumentResponse(BaseModel):
    file_id: str
    ready: bool = True
    pages: int = 0
    description: str | None = None

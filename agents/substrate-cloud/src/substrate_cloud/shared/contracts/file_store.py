"""Shared API contracts for artifact/file service."""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel


class FileUploadResponse(BaseModel):
    id: uuid.UUID
    thread_id: Optional[uuid.UUID] = None
    name: str
    mime: Optional[str] = None
    size: Optional[int] = None
    document_type: Optional[str] = None
    document_class: Optional[str] = None
    # Workspace-relative path — see routes/files.py::upload_file and
    # routes/chat_context.py::_session_relative_path, which derive it the
    # same way. Lets the UI open this file in the read-only artifact
    # viewer right after upload, not just after a reload.
    session_path: Optional[str] = None
    model_config = {"from_attributes": True}


class DirectUploadRequest(BaseModel):
    """Start an upload whose bytes go straight from the browser to object storage on signed URLs."""

    name: str
    size: int
    content_type: Optional[str] = None
    thread_id: Optional[uuid.UUID] = None
    sha256: Optional[str] = None


class UploadPart(BaseModel):
    part_number: int
    etag: str


class DirectUploadTicket(BaseModel):
    """Send the bytes to ``url`` with ``PUT`` and ``headers`` (or each ``parts[].url`` for its ``offset``/``length``), then
    ``POST /files/uploads/{id}/complete`` with the ETag each part answered."""

    id: uuid.UUID
    method: str  # "PUT" or "MULTIPART"
    url: Optional[str] = None
    headers: dict[str, str] = {}
    parts: list[dict] = []
    part_size: Optional[int] = None
    expires_at: Optional[str] = None
    session_path: Optional[str] = None


class DirectUploadComplete(BaseModel):
    parts: list[UploadPart] = []


class FileOut(BaseModel):
    id: uuid.UUID
    thread_id: Optional[uuid.UUID] = None
    name: str
    mime: Optional[str] = None
    size: Optional[int] = None
    document_type: Optional[str] = None
    document_class: Optional[str] = None
    model_config = {"from_attributes": True}


class FileUrlResponse(BaseModel):
    url: str
    expires_in: int = 3600

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
    model_config = {"from_attributes": True}


class FileOut(BaseModel):
    id: uuid.UUID
    thread_id: Optional[uuid.UUID] = None
    name: str
    mime: Optional[str] = None
    size: Optional[int] = None
    model_config = {"from_attributes": True}


class FileUrlResponse(BaseModel):
    url: str
    expires_in: int = 3600

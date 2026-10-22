from __future__ import annotations

from pydantic import BaseModel


class WorkspaceUsageResponse(BaseModel):
    used_bytes: int
    quota_bytes: int


class WorkspaceFileEntry(BaseModel):
    path: str
    name: str
    size_bytes: int
    modified_at: float
    session_id: str | None = None
    session_name: str | None = None
    owner: str = "user"


class WorkspaceFilesResponse(BaseModel):
    files: list[WorkspaceFileEntry]


class VersionOut(BaseModel):
    seq: int
    author: str
    checksum_sha256: str
    size_bytes: int
    created_at: float
    restored_from_seq: int | None = None


class VersionsResponse(BaseModel):
    versions: list[VersionOut]


class RestoreRequest(BaseModel):
    path: str
    seq: int
    session_id: str | None = None
    branch_id: str = "main"


class SaveFileRequest(BaseModel):
    path: str
    content: str
    session_id: str | None = None
    branch_id: str = "main"


class DeleteFileResponse(BaseModel):
    deleted: bool = True

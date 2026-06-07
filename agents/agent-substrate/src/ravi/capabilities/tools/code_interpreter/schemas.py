"""Request / response schemas for the Code Interpreter HTTP API.

Defined here (capabilities layer) so both the HTTP client and the
standalone service can import from a single source of truth without
either depending on the other.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ExecType(str, Enum):
    python = "python"
    bash = "bash"


class OutputType(str, Enum):
    text = "text"
    stderr = "stderr"
    image = "image"
    error = "error"
    file = "file"


class OutputItem(BaseModel):
    type: OutputType
    content: str
    name: str | None = None
    format: str | None = None
    encoding: str = "utf-8"


class ExecuteRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=256)
    code: str = Field(..., max_length=1_000_000)
    exec_type: ExecType = ExecType.python
    timeout: int = Field(default=30, ge=1, le=300)


class ExecuteResponse(BaseModel):
    success: bool
    session_id: str
    outputs: list[OutputItem] = []
    error: str | None = None
    execution_time: float = 0.0
    cell_id: str | None = None


class SessionDetail(BaseModel):
    session_id: str
    vm_id: str = ""
    vm_state: str = ""
    exec_count: int = 0
    age_seconds: int = 0
    idle_seconds: int = 0
    pod_name: str = ""


class SessionListResponse(BaseModel):
    sessions: list[SessionDetail]
    total: int
    pod_name: str = ""


class FileWriteRequest(BaseModel):
    path: str = Field(..., max_length=4096)
    content: str
    encoding: str = "utf-8"


class FileReadResponse(BaseModel):
    success: bool
    path: str | None = None
    content: str | None = None
    encoding: str = "utf-8"
    size: int = 0
    error: str | None = None


class InstallRequest(BaseModel):
    packages: list[str] = Field(..., min_length=1)


class HealthResponse(BaseModel):
    status: str
    pod_name: str
    pool_available: int
    pool_size: int
    pool_max: int
    active_sessions: int
    max_sessions: int
    uptime_seconds: float


__all__ = [
    "ExecType", "OutputType", "OutputItem",
    "ExecuteRequest", "ExecuteResponse",
    "SessionDetail", "SessionListResponse",
    "FileWriteRequest", "FileReadResponse",
    "InstallRequest", "HealthResponse",
]

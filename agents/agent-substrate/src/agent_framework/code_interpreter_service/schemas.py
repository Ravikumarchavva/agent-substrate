"""Request / response schemas for the Code Interpreter REST API.

These models are shared between the standalone service (routes.py)
and the HTTP client (http_client.py) in the main backend.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class ExecType(str, Enum):
    python = "python"
    bash = "bash"


class OutputType(str, Enum):
    """Kind of output produced by code execution."""
    text = "text"
    stderr = "stderr"
    image = "image"
    error = "error"
    file = "file"


# ── Outputs ──────────────────────────────────────────────────────────────────

class OutputItem(BaseModel):
    """A single output artefact from code execution."""
    type: OutputType
    content: str
    name: Optional[str] = None
    format: Optional[str] = None
    encoding: str = "utf-8"


# ── Execute ──────────────────────────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    """Run code in a persistent session VM."""
    session_id: str = Field(..., min_length=1, max_length=256)
    code: str = Field(..., max_length=1_000_000)
    exec_type: ExecType = ExecType.python
    timeout: int = Field(default=30, ge=1, le=300)


class ExecuteResponse(BaseModel):
    """Structured execution result with multimodal outputs."""
    success: bool
    session_id: str
    outputs: list[OutputItem] = []
    error: Optional[str] = None
    execution_time: float = 0.0
    cell_id: Optional[str] = None


# ── Sessions ─────────────────────────────────────────────────────────────────

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


# ── File operations ──────────────────────────────────────────────────────────

class FileWriteRequest(BaseModel):
    path: str = Field(..., max_length=4096)
    content: str
    encoding: str = "utf-8"


class FileReadResponse(BaseModel):
    success: bool
    path: Optional[str] = None
    content: Optional[str] = None
    encoding: str = "utf-8"
    size: int = 0
    error: Optional[str] = None


# ── Install ──────────────────────────────────────────────────────────────────

class InstallRequest(BaseModel):
    packages: list[str] = Field(..., min_length=1)


# ── Health ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    pod_name: str
    pool_available: int
    pool_size: int
    pool_max: int
    active_sessions: int
    max_sessions: int
    uptime_seconds: float

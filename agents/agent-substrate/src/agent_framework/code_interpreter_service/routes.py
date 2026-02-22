"""REST endpoints for the Code Interpreter service.

All endpoints are prefixed with ``/v1/``.
Authentication is via ``Bearer <token>`` header (optional, configurable).
"""

from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .schemas import (
    ExecuteRequest,
    ExecuteResponse,
    FileReadResponse,
    FileWriteRequest,
    HealthResponse,
    InstallRequest,
    OutputItem,
    OutputType,
    SessionDetail,
    SessionListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["code-interpreter"])


# ── Auth dependency ──────────────────────────────────────────────────────────

async def _verify_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Validate Bearer token if CI_AUTH_TOKEN is configured."""
    token = request.app.state.config.auth_token
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    if authorization.removeprefix("Bearer ") != token:
        raise HTTPException(403, "Invalid token")


Authed = Annotated[None, Depends(_verify_token)]


# ── Execute ──────────────────────────────────────────────────────────────────

@router.post("/execute", response_model=ExecuteResponse)
async def execute(body: ExecuteRequest, request: Request, _: Authed):
    """Execute code in a persistent session VM."""
    sm = request.app.state.session_manager
    cfg = request.app.state.config

    if len(body.code.encode("utf-8", errors="replace")) > cfg.max_code_size:
        raise HTTPException(413, f"Code exceeds {cfg.max_code_size} byte limit")

    timeout = min(body.timeout, cfg.max_timeout)

    if body.exec_type.value == "bash":
        guest_req = {"type": "bash", "cmd": body.code, "timeout": timeout}
    else:
        guest_req = {"type": "python", "code": body.code, "timeout": timeout}

    try:
        result = await sm.execute(body.session_id, guest_req)
    except RuntimeError as exc:
        if "Session limit" in str(exc):
            raise HTTPException(429, str(exc))
        raise HTTPException(503, str(exc))
    except Exception as exc:
        logger.error("Execute failed session=%s: %s", body.session_id, exc, exc_info=True)
        raise HTTPException(500, f"Execution error: {exc}")

    outputs = _build_outputs(result)

    return ExecuteResponse(
        success=result.get("success", False),
        session_id=body.session_id,
        outputs=outputs,
        error=result.get("error"),
        execution_time=result.get("execution_time", 0),
        cell_id=result.get("cell_id"),
    )


# ── Sessions ─────────────────────────────────────────────────────────────────

@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(request: Request, _: Authed):
    """List all active sessions on this pod."""
    sm = request.app.state.session_manager
    pod = request.app.state.config.pod_name
    sessions = sm.list_sessions()
    return SessionListResponse(
        sessions=[SessionDetail(pod_name=pod, **s) for s in sessions],
        total=len(sessions),
        pod_name=pod,
    )


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, request: Request, _: Authed):
    """Get details for a specific session."""
    sm = request.app.state.session_manager
    for s in sm.list_sessions():
        if s["session_id"] == session_id:
            return SessionDetail(pod_name=request.app.state.config.pod_name, **s)
    raise HTTPException(404, f"Session '{session_id}' not found")


@router.delete("/sessions/{session_id}")
async def destroy_session(session_id: str, request: Request, _: Authed):
    """Destroy a session and its VM immediately."""
    sm = request.app.state.session_manager
    await sm.destroy_session(session_id)
    return {"status": "destroyed", "session_id": session_id}


@router.post("/sessions/{session_id}/reset")
async def reset_session(session_id: str, request: Request, _: Authed):
    """Clear Python namespace without destroying the VM."""
    sm = request.app.state.session_manager
    result = await sm.reset_session(session_id)
    return {"status": "reset", "session_id": session_id, "result": result}


@router.get("/sessions/{session_id}/state")
async def get_session_state(session_id: str, request: Request, _: Authed):
    """Retrieve defined variables and execution count."""
    sm = request.app.state.session_manager
    result = await sm.execute(session_id, {"type": "get_state"})
    return result


# ── File operations ──────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/files/write")
async def write_file(session_id: str, body: FileWriteRequest, request: Request, _: Authed):
    """Write a file into the session VM."""
    sm = request.app.state.session_manager
    req_type = "write_file_b" if body.encoding == "base64" else "write_file"
    result = await sm.execute(
        session_id, {"type": req_type, "path": body.path, "content": body.content},
    )
    return result


@router.get("/sessions/{session_id}/files/read", response_model=FileReadResponse)
async def read_file(session_id: str, path: str, request: Request, _: Authed):
    """Read a text file from the session VM."""
    sm = request.app.state.session_manager
    result = await sm.execute(session_id, {"type": "read_file", "path": path})
    return FileReadResponse(
        success=result.get("success", False),
        path=result.get("path"),
        content=result.get("content"),
        error=result.get("error"),
    )


@router.get("/sessions/{session_id}/files/read_binary")
async def read_file_binary(session_id: str, path: str, request: Request, _: Authed):
    """Read a binary file from the session VM (base64-encoded)."""
    sm = request.app.state.session_manager
    result = await sm.execute(session_id, {"type": "read_file_b", "path": path})
    return result


# ── Install packages ─────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/install")
async def install_packages(session_id: str, body: InstallRequest, request: Request, _: Authed):
    """pip-install packages into the session VM."""
    sm = request.app.state.session_manager
    result = await sm.execute(
        session_id, {"type": "install", "packages": body.packages},
    )
    return result


# ── Health ───────────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    """Liveness probe — no auth required."""
    sm = request.app.state.session_manager
    cfg = request.app.state.config
    pool = sm._pool

    available = pool.available
    active = sm.session_count

    if available == 0 and active >= cfg.max_sessions:
        status = "unhealthy"
    elif available < cfg.pool_size // 2:
        status = "degraded"
    else:
        status = "healthy"

    return HealthResponse(
        status=status,
        pod_name=cfg.pod_name,
        pool_available=available,
        pool_size=cfg.pool_size,
        pool_max=cfg.pool_max_size,
        active_sessions=active,
        max_sessions=cfg.max_sessions,
        uptime_seconds=round(time.monotonic() - request.app.state.start_time, 1),
    )


@router.get("/health/ready")
async def readiness(request: Request):
    """k8s readiness probe — 503 if no VMs available."""
    sm = request.app.state.session_manager
    if sm._pool.available > 0:
        return {"ready": True}
    raise HTTPException(503, "No VMs available in pool")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_outputs(result: dict) -> list[OutputItem]:
    """Convert guest-agent result dict to structured OutputItem list.

    Supports both v3 structured ``outputs[]`` and v2 flat-field fallback.
    """
    outputs: list[OutputItem] = []

    # v3 agent returns structured outputs
    if "outputs" in result and result["outputs"]:
        for o in result["outputs"]:
            outputs.append(OutputItem(
                type=o.get("type", "text"),
                content=o.get("content", ""),
                name=o.get("name"),
                format=o.get("format"),
                encoding=o.get("encoding", "utf-8"),
            ))
        return outputs

    # v2 fallback — flat output/stderr/error fields
    if result.get("output"):
        outputs.append(OutputItem(type=OutputType.text, content=result["output"]))
    if result.get("stderr"):
        outputs.append(OutputItem(type=OutputType.stderr, content=result["stderr"], name="stderr"))
    if result.get("error") and not result.get("success"):
        outputs.append(OutputItem(type=OutputType.error, content=result["error"]))

    return outputs

"""HTTP proxy routes for the code-interpreter service.

These routes forward requests from the main backend to the
code-interpreter pod(s) via the CodeInterpreterClient.

POST /api/execute          — execute code in a session
GET  /api/execute/health   — aggregated health from all CI pods
GET  /api/execute/sessions — list sessions across all CI pods
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/execute", tags=["code-interpreter"])


# ── Request / Response models ────────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    code: str = Field(..., max_length=1_000_000, description="Python or bash code")
    exec_type: str = Field(default="python", description="'python' or 'bash'")
    timeout: int = Field(default=30, ge=1, le=300, description="Max seconds")
    session_id: str = Field(default="", description="Session ID (defaults to thread)")


class ExecuteResponse(BaseModel):
    success: bool
    output: str = ""
    error: str | None = None
    execution_time: float = 0.0
    cell_id: str | None = None
    images: list[dict] | None = None


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("", response_model=ExecuteResponse)
async def execute_code(body: ExecuteRequest, request: Request):
    """Execute code via the code-interpreter service."""
    client = getattr(request.app.state, "ci_client", None)
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Code interpreter service not available. Set CODE_INTERPRETER_URL.",
        )

    session_id = body.session_id or "api-default"

    try:
        resp = await client.execute(
            session_id=session_id,
            code=body.code,
            exec_type=body.exec_type,
            timeout=body.timeout,
        )
    except Exception as e:
        logger.error("execute_code proxy failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Service error: {e}")

    # Convert multimodal outputs to flat response
    text_parts = []
    images = []
    for output in resp.outputs:
        t = output.type.value
        if t == "text":
            text_parts.append(output.content)
        elif t == "stderr":
            text_parts.append(f"[stderr] {output.content}")
        elif t == "error":
            text_parts.append(f"[error] {output.content}")
        elif t == "image":
            images.append({
                "name": output.name or "figure.png",
                "format": output.format or "png",
                "data": output.content,
            })
            text_parts.append(f"[Generated {output.name or 'figure.png'}]")

    return ExecuteResponse(
        success=resp.success,
        output="\n".join(text_parts) if text_parts else "",
        error=resp.error,
        execution_time=resp.execution_time,
        cell_id=resp.cell_id,
        images=images if images else None,
    )


@router.get("/health")
async def pool_health(request: Request):
    """Aggregated health from all code-interpreter pods."""
    client = getattr(request.app.state, "ci_client", None)
    if client is None:
        return {"status": "disabled", "pods": []}

    try:
        pods = await client.health_all_pods()
        return {
            "status": "healthy" if all(p.status == "healthy" for p in pods) else "degraded",
            "pods": [p.model_dump() for p in pods],
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.get("/sessions")
async def list_sessions(request: Request):
    """List sessions across all code-interpreter pods."""
    client = getattr(request.app.state, "ci_client", None)
    if client is None:
        return {"sessions": [], "total": 0}

    try:
        all_pods = await client.list_sessions_all_pods()
        sessions = []
        for pod_resp in all_pods:
            sessions.extend([s.model_dump() for s in pod_resp.sessions])
        return {"sessions": sessions, "total": len(sessions)}
    except Exception as e:
        return {"sessions": [], "total": 0, "error": str(e)}

"""Tool Executor: HTTP routes.

  POST /api/v1/tools/execute         execute a tool call as the caller
  GET  /api/v1/tools                 list available tools
  GET  /api/v1/tools/{name}/schema   a tool's schema

The files a code-interpreter run produces are saved to the caller's own file store (``outputs.py``) and replaced in the result by
``file_ref`` entries.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from substrate_identity import Forbidden

from substrate_cloud.services.tool_executor.executor import execute_tool
from substrate_cloud.services.tool_executor.outputs import save_file_outputs
from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.auth.middleware import get_current_user
from substrate_cloud.shared.problem import Problem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolExecuteBody(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = {}
    tool_call_id: str = ""
    thread_id: Optional[uuid.UUID] = None
    timeout: float = 120.0


class ToolExecuteResult(BaseModel):
    tool_name: str
    tool_call_id: str
    content: str
    is_error: bool
    metadata: Dict[str, Any] = {}


def _token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else ""


@router.post("/execute")
async def execute_tool_endpoint(
    body: ToolExecuteBody,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
) -> ToolExecuteResult:
    """Execute a tool by name for the caller."""
    if not user.tenant_id:
        raise Forbidden(
            "Tools run for a user of an organization, not for a service account"
        )
    if body.thread_id is not None:
        # A thread that is not the caller's is a 404, so output cannot be filed under someone else's conversation.
        await request.app.state.conversation_client.request(
            "GET", f"/threads/{body.thread_id}", _token(request)
        )

    result = await execute_tool(
        registry=request.app.state.tool_registry,
        tool_name=body.tool_name,
        arguments=body.arguments,
        tool_call_id=body.tool_call_id,
        timeout=body.timeout,
    )
    if (
        body.tool_name == "code_interpreter"
        and body.thread_id is not None
        and not result.get("is_error")
    ):
        result = await save_file_outputs(
            result,
            file_access=request.app.state.file_access,
            user=user,
            thread_id=body.thread_id,
        )
    return ToolExecuteResult(**result)


@router.get("")
async def list_tools(request: Request) -> list:
    return request.app.state.tool_registry.list_tools()


@router.get("/{tool_name}/schema")
async def get_tool_schema(tool_name: str, request: Request) -> dict:
    tool = request.app.state.tool_registry.get(tool_name)
    if not tool:
        raise Problem(404, "not-found", f"Tool '{tool_name}' not found")
    try:
        return tool.get_openai_schema()
    except Exception:
        return tool.get_schema().__dict__

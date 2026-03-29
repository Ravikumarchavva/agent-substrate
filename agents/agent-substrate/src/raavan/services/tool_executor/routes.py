"""Tool Executor — HTTP routes.

Routes:
  POST /tools/execute         – execute a tool call
  GET  /tools                 – list available tools
  GET  /tools/{name}/schema   – get tool schema

File output bridge (code_interpreter):
  After code_interpreter execution, any file outputs in the response
  (ExecuteResponse.outputs items with type="file") are automatically
  saved to the Artifact service and replaced with stable file_id references.
  This means the agent doesn't need to handle base64 blobs — it just gets
  back a file_id it can use with the file_manager tool.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from raavan.services.tool_executor.executor import execute_tool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolExecuteBody(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = {}
    tool_call_id: str = ""
    run_id: Optional[str] = None
    thread_id: Optional[str] = None
    timeout: float = 120.0


class ToolExecuteResult(BaseModel):
    tool_name: str
    tool_call_id: str
    content: str
    is_error: bool
    metadata: Dict[str, Any] = {}


async def _save_ci_file_outputs(
    result: Dict[str, Any],
    thread_id: str,
    artifact_url: str,
) -> Dict[str, Any]:
    """Save base64 file outputs from code_interpreter to the Artifact service.

    Replaces in-line base64 blobs with {type: "file_ref", file_id: "..."} so
    the agent sees stable references it can pass to file_manager or share.
    """
    outputs: List[Dict[str, Any]] = result.get("metadata", {}).get("outputs", [])
    if not outputs:
        return result

    saved: List[Dict[str, Any]] = []
    file_refs: List[Dict[str, Any]] = []

    for item in outputs:
        if item.get("type") != "file":
            saved.append(item)
            continue

        raw_content = item.get("content", "")
        filename = item.get("name") or "output"
        fmt = item.get("format") or ""
        encoding = item.get("encoding", "utf-8")

        try:
            file_bytes = (
                base64.b64decode(raw_content)
                if encoding == "base64"
                else raw_content.encode(encoding)
            )
        except Exception as exc:
            logger.warning("CI output decode failed (%s): %s", filename, exc)
            saved.append(item)
            continue

        content_type = _mime_for(filename, fmt)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{artifact_url}/{thread_id}/files",
                    files={"file": (filename, file_bytes, content_type)},
                )
                resp.raise_for_status()
                file_meta = resp.json()
                file_id = file_meta.get("id") or file_meta.get("file_id", "")
                file_refs.append(
                    {
                        "type": "file_ref",
                        "file_id": file_id,
                        "name": filename,
                        "content_type": content_type,
                    }
                )
                saved.append({"type": "file_ref", "file_id": file_id, "name": filename})
        except Exception as exc:
            logger.warning("Failed to save CI file output '%s': %s", filename, exc)
            saved.append(item)  # leave original on failure

    if file_refs:
        # Mention saved files in the text content so the agent knows about them
        names = ", ".join(r["name"] for r in file_refs)
        result = dict(result)
        result["content"] = (result.get("content") or "") + (
            f"\n\n[Files saved: {names}. "
            f"Use file_manager to access file_ids: "
            f"{', '.join(r['file_id'] for r in file_refs)}]"
        )
        meta = dict(result.get("metadata") or {})
        meta["outputs"] = saved
        meta["saved_files"] = file_refs
        result["metadata"] = meta

    return result


def _mime_for(filename: str, fmt: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else fmt.lower()
    _map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "svg": "image/svg+xml",
        "pdf": "application/pdf",
        "csv": "text/csv",
        "json": "application/json",
        "txt": "text/plain",
        "html": "text/html",
        "zip": "application/zip",
    }
    return _map.get(ext, "application/octet-stream")


@router.post("/execute")
async def execute_tool_endpoint(body: ToolExecuteBody, request: Request):
    """Execute a tool by name. Used by Agent Runtime for tool dispatch.

    For code_interpreter calls, file outputs are transparently saved to
    the Artifact service and replaced with file_id references.
    """
    registry = request.app.state.tool_registry
    result = await execute_tool(
        registry=registry,
        tool_name=body.tool_name,
        arguments=body.arguments,
        tool_call_id=body.tool_call_id,
        timeout=body.timeout,
    )

    # File output bridge: save CI-generated files to Artifact service
    if (
        body.tool_name == "code_interpreter"
        and body.thread_id
        and not result.get("is_error")
        and hasattr(request.app.state, "artifact_url")
    ):
        result = await _save_ci_file_outputs(
            result,
            thread_id=body.thread_id,
            artifact_url=request.app.state.artifact_url,
        )

    return ToolExecuteResult(**result)


@router.get("")
async def list_tools(request: Request):
    """List all available tools with their schemas."""
    registry = request.app.state.tool_registry
    return registry.list_tools()


@router.get("/{tool_name}/schema")
async def get_tool_schema(tool_name: str, request: Request):
    """Get the schema for a specific tool."""
    registry = request.app.state.tool_registry
    tool = registry.get(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    try:
        return tool.get_openai_schema()
    except Exception:
        return tool.get_schema().__dict__

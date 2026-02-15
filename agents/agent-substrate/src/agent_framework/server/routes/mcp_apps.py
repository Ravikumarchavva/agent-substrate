"""MCP Apps – serve UI resources for interactive tool UIs.

GET  /ui/{resource_name}              – serve bundled HTML app for rendering inside an iframe
GET  /mcp-apps/manifest               – list available MCP App tools with their UI metadata
POST /threads/{thread_id}/mcp-context – update model context from interactive MCP App
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List
from agent_framework.tools.base_tool import BaseTool

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.server.database import get_db
from agent_framework.server.schemas import McpContextUpdate
from agent_framework.server.services import create_step, get_thread

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp-apps"])

# ── Registry ─────────────────────────────────────────────────────────────────
# Maps resource names (path component after ui://) → absolute file paths
# Tools register themselves via register_app_resource()

_ui_resources: Dict[str, Path] = {}

# Built-in apps directory
_APPS_DIR = Path(__file__).resolve().parent.parent.parent / "mcp_apps"


def register_app_resource(name: str, html_path: Path) -> str:
    """Register an HTML file to be served as a ui:// resource.

    Args:
        name: unique resource name (used in ui://{name})
        html_path: absolute path to the HTML file

    Returns:
        The full ui:// URI that can be put in tool _meta.ui.resourceUri
    """
    _ui_resources[name] = html_path
    return f"ui://{name}"


def get_resource_http_url(name: str, base_url: str = "") -> str:
    """Convert a ui:// resource name to its HTTP serving URL.

    This is used by the SSE layer to tell the frontend WHERE to
    fetch the HTML for the sandboxed iframe.
    """
    return f"{base_url}/ui/{name}"


def resolve_ui_uri(uri: str, base_url: str = "") -> str | None:
    """Convert ``ui://name`` → ``http://host/ui/name``.

    Returns None if the URI is not a ui:// scheme.
    """
    if not uri.startswith("ui://"):
        return None
    name = uri.removeprefix("ui://")
    return get_resource_http_url(name, base_url)


# ── Auto-discover built-in apps ─────────────────────────────────────────────

def _discover_builtin_apps() -> None:
    """Scan the mcp_apps/ directory for *.html files and register them."""
    if not _APPS_DIR.exists():
        return
    for html_file in _APPS_DIR.glob("*.html"):
        name = html_file.stem  # e.g., "time_picker" from "time_picker.html"
        register_app_resource(name, html_file)
        logger.info("Registered built-in MCP App: ui://%s", name)


_discover_builtin_apps()


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/ui/{resource_name}", response_class=HTMLResponse)
async def serve_ui_resource(resource_name: str):
    """Serve a registered MCP App HTML resource.

    The frontend renders this inside a sandboxed ``<iframe>`` with
    ``allow-scripts`` so it can communicate via ``postMessage``.
    """
    html_path = _ui_resources.get(resource_name)
    if html_path is None or not html_path.exists():
        raise HTTPException(status_code=404, detail=f"UI resource '{resource_name}' not found")

    html = html_path.read_text(encoding="utf-8")
    return HTMLResponse(
        content=html,
        headers={
            # Allow embedding in iframes from any origin (dev mode)
            # In production, set this to your specific frontend origin
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'unsafe-inline' https://sdk.scdn.co; "
                "style-src 'unsafe-inline'; "
                "img-src * data:; "
                "media-src *; "
                "connect-src *; "
                "frame-src https://sdk.scdn.co; "
                "frame-ancestors *; "
            ),
        },
    )


@router.get("/mcp-apps/manifest")
async def get_manifest(request: Request) -> List[Dict[str, Any]]:
    """Return metadata about all available MCP App tools.

    The frontend can use this to know which tools have interactive UIs
    and pre-fetch their HTML resources.
    """
    tools: list[BaseTool] = getattr(request.app.state, "tools", [])
    manifest: List[Dict[str, Any]] = []

    for tool in tools:
        schema = tool.get_schema()
        if schema.meta and schema.meta.get("ui", {}).get("resourceUri"):
            uri = schema.meta["ui"]["resourceUri"]
            name = uri.removeprefix("ui://") if uri.startswith("ui://") else uri
            manifest.append({
                "tool_name": schema.name,
                "description": schema.description,
                "resource_uri": uri,
                "http_url": f"/ui/{name}",
                "annotations": schema.annotations,
            })

    return manifest


# ── MCP App context update ───────────────────────────────────────────────────

@router.post("/threads/{thread_id}/mcp-context")
async def update_mcp_context(
    thread_id: uuid.UUID,
    body: McpContextUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Store a model context update from an interactive MCP App.

    When a user interacts with an MCP App (e.g., drags tasks on a Kanban board),
    the app sends the updated state here. This is stored as a step so the LLM
    sees the latest state in its next turn (per MCP Apps spec ui/update-model-context).
    """
    thread = await get_thread(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Serialize the context to a human-readable string for the LLM
    context_str = json.dumps(body.context, indent=2) if not isinstance(body.context, str) else body.context

    await create_step(
        db,
        thread_id=thread_id,
        type="mcp_app_context",
        name=body.tool_name,
        output=context_str,
        metadata={"tool_name": body.tool_name, "source": "mcp_app"},
    )
    await db.commit()

    return {"status": "ok"}

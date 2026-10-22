from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.conversations.mcp_apps.model import McpContextUpdate, McpManifest
from loamz.server.agents.features.conversations.mcp_apps.service import (
    CONTENT_SECURITY_POLICY,
    McpAppsService,
    mcp_apps,
)

Service = Annotated[McpAppsService, Depends(use(mcp_apps))]

router = APIRouter(prefix="/api/v1", tags=["mcp-apps"])


@router.get("/ui/{resource_name}", response_class=HTMLResponse)
async def serve_ui_resource(resource_name: str, _: CallerDep, service: Service) -> HTMLResponse:
    """A bundled MCP app page, rendered by the console in a sandboxed iframe (`allow-scripts`, postMessage)."""
    return HTMLResponse(
        content=service.resource(resource_name),
        media_type="text/html;profile=mcp-app",
        headers={"Content-Security-Policy": CONTENT_SECURITY_POLICY},
    )


@router.get("/mcp-apps/manifest", response_model=McpManifest)
async def get_manifest(_: CallerDep, service: Service) -> McpManifest:
    """The tools that have interactive UIs, so the console can pre-fetch their pages."""
    return service.manifest()


@router.post("/threads/{thread_id}/mcp-context")
async def update_mcp_context(
    thread_id: str, body: McpContextUpdate, caller: CallerDep, service: Service
) -> dict[str, str]:
    await service.update_context(caller, thread_id, body)
    return {"status": "ok"}

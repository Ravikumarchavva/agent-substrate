"""MCP Apps – serve UI resources for interactive tool UIs.

GET  /ui/{resource_name}              – serve bundled HTML app for rendering inside an iframe
GET  /mcp-apps/manifest               – list available MCP App tools with their UI metadata
POST /threads/{thread_id}/mcp-context – update model context from interactive MCP App
"""

from __future__ import annotations
from agent_substrate.logger import setup_logging

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List
from pydantic import BaseModel
from agent_substrate.kernel.tools import Tool

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agent_substrate.serving.monolith.database import get_db
from agent_substrate.serving.monolith.schemas import McpContextUpdate
from agent_substrate.serving.monolith.security.deps import get_current_user
from agent_substrate.serving.monolith.services import create_step, get_thread

logger = setup_logging()

router = APIRouter(tags=["mcp-apps"], dependencies=[Depends(get_current_user)])

# ── Registry ─────────────────────────────────────────────────────────────────
# Maps resource names (path component after ui://) → absolute file paths
# Tools register themselves via register_app_resource()

_ui_resources: Dict[str, Path] = {}

# Built-in apps directory — bundled MCP App HTML lives under ravi/adapters/mcp/apps.
# __file__ = ravi/serving/monolith/routes/mcp_apps.py → parents[3] = ravi/
_APPS_DIR = Path(__file__).resolve().parents[3] / "adapters" / "mcp" / "apps"

_MCP_APP_THEME_STYLE = """
<style id="ravi-mcp-theme-vars">
:root {
    color-scheme: dark;
    --mcp-bg: #171717;
    --mcp-surface: #1f1f1f;
    --mcp-surface-muted: #262626;
    --mcp-surface-strong: #111111;
    --mcp-surface-elevated: #0b0b0d;
    --mcp-border: #2a2a2a;
    --mcp-border-strong: #3f3f46;
    --mcp-text: #f5f5f5;
    --mcp-text-muted: #a1a1aa;
    --mcp-text-soft: #71717a;
    --mcp-shadow: rgba(0, 0, 0, 0.24);
    --mcp-overlay: rgba(0, 0, 0, 0.5);
    --mcp-code-bg: #18181b;
    --mcp-highlight: rgba(37, 99, 235, 0.14);
    --mcp-accent: #2563eb;
    --mcp-accent-soft: rgba(37, 99, 235, 0.14);
    --mcp-success: #22c55e;
    --mcp-success-soft: rgba(34, 197, 94, 0.12);
    --mcp-warning: #f59e0b;
    --mcp-warning-soft: rgba(245, 158, 11, 0.14);
    --mcp-danger: #ef4444;
    --mcp-danger-soft: rgba(239, 68, 68, 0.14);
}

html[data-theme="light"] {
    color-scheme: light;
    --mcp-bg: #f7f4ee;
    --mcp-surface: #ffffff;
    --mcp-surface-muted: #f2ede3;
    --mcp-surface-strong: #ebe4d8;
    --mcp-surface-elevated: #fcfaf6;
    --mcp-border: #e2d9ca;
    --mcp-border-strong: #cfc3b0;
    --mcp-text: #171717;
    --mcp-text-muted: #5f5b53;
    --mcp-text-soft: #8b857a;
    --mcp-shadow: rgba(15, 23, 42, 0.08);
    --mcp-overlay: rgba(15, 23, 42, 0.18);
    --mcp-code-bg: #f5efe5;
    --mcp-highlight: rgba(37, 99, 235, 0.1);
    --mcp-accent: #2563eb;
    --mcp-accent-soft: rgba(37, 99, 235, 0.1);
    --mcp-success: #16a34a;
    --mcp-success-soft: rgba(22, 163, 74, 0.1);
    --mcp-warning: #d97706;
    --mcp-warning-soft: rgba(217, 119, 6, 0.12);
    --mcp-danger: #dc2626;
    --mcp-danger-soft: rgba(220, 38, 38, 0.1);
}

html, body {
    background: var(--mcp-bg);
    color: var(--mcp-text);
}

body {
    accent-color: var(--mcp-accent);
}

button, input, select, textarea {
    font: inherit;
}

::selection {
    background: var(--mcp-highlight);
}
</style>
"""

_MCP_APP_THEME_SCRIPT = """
<script id="ravi-mcp-theme-bridge">
(function () {
    if (window.__RAVI_MCP_THEME_BRIDGE__) {
        return;
    }

    const bridge = {
        initRequestId: null,
        applyTheme(theme) {
            const resolved = theme === "light" ? "light" : "dark";
            document.documentElement.setAttribute("data-theme", resolved);
        },
        requestInit() {
            this.initRequestId = "ravi-theme-init-" + Math.random().toString(36).slice(2);
            window.parent.postMessage(
                { jsonrpc: "2.0", id: this.initRequestId, method: "ui/initialize" },
                "*"
            );
        },
    };

    window.__RAVI_MCP_THEME_BRIDGE__ = bridge;

    if (window.matchMedia) {
        bridge.applyTheme(
            window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"
        );
    } else {
        bridge.applyTheme("dark");
    }

    window.addEventListener("message", function (event) {
        const msg = event.data;
        if (!msg || typeof msg !== "object" || msg.jsonrpc !== "2.0") {
            return;
        }

        if (msg.id === bridge.initRequestId && msg.result && typeof msg.result === "object") {
            const hostContext = msg.result.hostContext || {};
            if (typeof hostContext.theme === "string") {
                bridge.applyTheme(hostContext.theme);
            }
            return;
        }

        if (msg.method === "ui/notifications/theme-changed" && msg.params) {
            if (typeof msg.params.theme === "string") {
                bridge.applyTheme(msg.params.theme);
            }
        }
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () {
            bridge.requestInit();
        }, { once: true });
    } else {
        bridge.requestInit();
    }
})();
</script>
"""


def _inject_theme_bridge(html: str) -> str:
    if "ravi-mcp-theme-bridge" in html:
        return html

    injection = f"{_MCP_APP_THEME_STYLE}\n{_MCP_APP_THEME_SCRIPT}"
    if "</head>" in html:
        return html.replace("</head>", f"{injection}\n</head>", 1)

    return f"{injection}\n{html}"


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
    """Scan the bundled integrations/mcp/apps directory for *.html files."""
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
        raise HTTPException(
            status_code=404, detail=f"UI resource '{resource_name}' not found"
        )

    html = _inject_theme_bridge(html_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        content=html,
        media_type="text/html;profile=mcp-app",
        headers={
            # Allow embedding in iframes from any origin (dev mode)
            # In production, set this to your specific frontend origin
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://sdk.scdn.co blob:; "
                "style-src 'self' 'unsafe-inline'; "
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
    raw_tools = getattr(request.app.state, "tools", [])
    # app.state.tools is a Toolbox (not a plain list) — call .all() to iterate
    tool_list: list[Tool] = (
        raw_tools.all() if hasattr(raw_tools, "all") else list(raw_tools)
    )
    manifest: List[Dict[str, Any]] = []

    for tool in tool_list:
        ui = getattr(tool, "ui", None)
        if ui is None:
            continue
        uri = ui.resource_uri
        name = uri.removeprefix("ui://") if uri.startswith("ui://") else uri
        manifest.append(
            {
                "tool_name": getattr(tool, "name", ""),
                "description": getattr(tool, "description", ""),
                "resource_uri": uri,
                "http_url": f"/ui/{name}",
                "permissions": list(ui.permissions),
                "prefers_border": ui.prefers_border,
            }
        )

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

    # Serialize the context to a human-readable string for the LLM.
    # body.context can be a dict, list, str, or a Pydantic model (McpAppContextPayload).
    raw = body.context
    if isinstance(raw, BaseModel):
        raw = raw.model_dump()
    context_str = json.dumps(raw, indent=2) if not isinstance(raw, str) else raw

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

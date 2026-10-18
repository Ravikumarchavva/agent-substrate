from __future__ import annotations

import json
from pathlib import Path

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.conversations.chat.service import ChatService, chat
from loamz.agents.features.conversations.mcp_apps.model import (
    McpAppEntry,
    McpContextUpdate,
    McpManifest,
)
from loamz.agents.features.conversations.mcp_apps.theme import inject_theme_bridge
from loamz.agents.features.conversations.threads.service import ThreadsService, threads
from loamz.agents.kernel.caller import Caller
from loamz.agents.toolkit import mcp as _mcp_package
from loamz.data.storage.errors import NotFoundError

APPS_DIR = Path(_mcp_package.__file__).resolve().parent / "apps"

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://sdk.scdn.co blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src * data:; "
    "media-src *; "
    "connect-src *; "
    "frame-src https://sdk.scdn.co; "
    "frame-ancestors *; "
)


class McpAppsService:
    """The interactive tool UIs: the bundled app pages, which tools use them, and the context they send back."""

    def __init__(self, threads_: ThreadsService, chat_: ChatService, apps_dir: Path = APPS_DIR) -> None:
        self._threads = threads_
        self._chat = chat_
        self._apps = {p.stem: p for p in apps_dir.glob("*.html")} if apps_dir.exists() else {}

    def resource(self, name: str) -> str:
        """The page for `ui://name`, with the theme bridge injected."""
        path = self._apps.get(name)
        if path is None or not path.exists():
            raise NotFoundError(f"UI resource '{name}' not found")
        return inject_theme_bridge(path.read_text(encoding="utf-8"))

    def manifest(self) -> McpManifest:
        items: list[McpAppEntry] = []
        for tool in self._chat.tool_catalog():
            ui = getattr(tool, "ui", None)
            if ui is None:
                continue
            uri = ui.resource_uri
            name = uri.removeprefix("ui://") if uri.startswith("ui://") else uri
            items.append(
                McpAppEntry(
                    tool_name=getattr(tool, "name", ""),
                    description=getattr(tool, "description", ""),
                    resource_uri=uri,
                    http_url=f"/api/v1/ui/{name}",
                    permissions=list(ui.permissions),
                    prefers_border=ui.prefers_border,
                )
            )
        return McpManifest(items=items)

    async def update_context(self, caller: Caller, thread_id: str, body: McpContextUpdate) -> None:
        """Log an app's state on the thread's run, so the model reads it on its next turn (MCP Apps
        `ui/update-model-context`)."""
        await self._threads.require(caller, thread_id)
        await self._threads.journal.annotate(
            thread_id,
            "mcp_app_context",
            {"tool_name": body.tool_name, "context": json.dumps(body.context, indent=2)},
        )


def mcp_apps(container: Container) -> McpAppsService:
    return McpAppsService(container.get(threads), container.get(chat))

"""MCP App-enabled tools with interactive UIs.

These tools declare ``_meta.ui.resourceUri`` so the frontend renders
a sandboxed iframe with the interactive HTML app alongside their output.
"""

from __future__ import annotations
from ravi.logger import setup_logging

import asyncio
import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Dict, List

import httpx

from ravi.config import settings
from ravi.kernel.tools.base_tool import ToolResult, ToolRisk
from ravi.adapters.mcp.app_tool_base import McpAppTool
from ravi.serving.monolith.routes.workspace_oauth import (
    clear_workspace_tokens_async,
    get_workspace_access_token_async,
)

logger = setup_logging()

_SPOTIFY_TOKEN_PATH = "/api/spotify/token"


async def _is_spotify_authenticated_async() -> bool:
    """Check asynchronously if the user has authenticated with Spotify OAuth.

    Replaces the blocking ``requests.get`` with an ``httpx.AsyncClient`` call
    so it is safe to call from the asyncio event loop.
    """
    url = settings.FRONTEND_URL.rstrip("/") + _SPOTIFY_TOKEN_PATH
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
            if resp.is_success:
                return bool(resp.json().get("authenticated"))
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Data Visualizer
# ---------------------------------------------------------------------------


class DataVisualizerTool(McpAppTool):
    """Visualise structured data as interactive bar / line / pie charts."""

    ui_resource_uri: ClassVar[str] = "ui://data_visualizer"
    risk: ClassVar[ToolRisk] = ToolRisk.SAFE  # read-only data rendering

    def __init__(self) -> None:
        super().__init__(
            name="data_visualizer",
            description=(
                "Render an interactive chart from structured data. "
                "Provide data as an array of {label, value} objects, "
                "or as parallel labels/values arrays, or as an object "
                "with numeric values. The user will see a live chart "
                "with bar, line, and pie views plus summary statistics."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Chart title",
                    },
                    "data": {
                        "type": "array",
                        "description": "Array of {label, value} data points",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "number"},
                            },
                            "required": ["label", "value"],
                        },
                    },
                },
                "required": ["data"],
                "additionalProperties": False,
            },
            annotations={
                "readOnlyHint": True,
                "openWorldHint": False,
                "title": "Data Visualizer",
            },
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        data: List[Dict[str, Any]],
        title: str = "Chart",
    ) -> ToolResult:

        if not data:
            return ToolResult(
                content=[{"type": "text", "text": "No data provided."}],
                is_error=True,
            )

        values = [d.get("value", 0) for d in data]
        total = sum(values)
        avg = total / len(values) if values else 0

        summary = (
            f"**{title}**\n"
            f"Items: {len(data)} | Total: {total} | "
            f"Avg: {avg:.1f} | Max: {max(values)} | Min: {min(values)}"
        )

        return ToolResult(
            content=[{"type": "text", "text": summary}],
            is_error=False,
        )


# ---------------------------------------------------------------------------
# Markdown Previewer
# ---------------------------------------------------------------------------


class MarkdownPreviewerTool(McpAppTool):
    """Render markdown content with a live preview / source toggle."""

    ui_resource_uri: ClassVar[str] = "ui://markdown_previewer"
    risk: ClassVar[ToolRisk] = ToolRisk.SAFE  # rendering only, no I/O

    def __init__(self) -> None:
        super().__init__(
            name="markdown_previewer",
            description=(
                "Render markdown text as a rich interactive preview. "
                "Provide the content as a markdown string. The user will "
                "see a formatted preview with headings, lists, tables, "
                "code blocks, and can toggle between preview and source."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title for the preview panel",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown text to render",
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
            },
            annotations={
                "readOnlyHint": True,
                "openWorldHint": False,
                "title": "Markdown Previewer",
            },
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        content: str,
        title: str = "",
    ) -> ToolResult:
        if not content:
            return ToolResult(
                content=[{"type": "text", "text": "No markdown content provided."}],
                is_error=True,
            )

        lines = content.strip().split("\n")
        words = content.split()
        summary = f"Rendered markdown preview: {len(lines)} lines, {len(words)} words"
        return ToolResult(
            content=[{"type": "text", "text": summary}],
            is_error=False,
        )


# ---------------------------------------------------------------------------
# JSON Explorer
# ---------------------------------------------------------------------------


class JsonExplorerTool(McpAppTool):
    """Display structured data in an interactive collapsible tree."""

    ui_resource_uri: ClassVar[str] = "ui://json_explorer"
    risk: ClassVar[ToolRisk] = ToolRisk.SAFE  # read-only display

    def __init__(self) -> None:
        super().__init__(
            name="json_explorer",
            description=(
                "Display any structured data as an interactive JSON tree. "
                "The user can expand/collapse nodes, search keys and values, "
                "and copy individual values. Pass `data` as a JSON-encoded "
                "object or array."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title for the explorer panel",
                    },
                    "data": {
                        "type": "string",
                        "description": "JSON-encoded object or array to explore",
                    },
                },
                "required": ["data"],
                "additionalProperties": False,
            },
            annotations={
                "readOnlyHint": True,
                "openWorldHint": False,
                "title": "JSON Explorer",
            },
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        data: Any,
        title: str = "",
    ) -> ToolResult:
        if data is None:
            return ToolResult(
                content=[{"type": "text", "text": "No data provided."}],
                is_error=True,
            )

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": "`data` must be valid JSON text representing an object or array.",
                        }
                    ],
                    is_error=True,
                )

        def count_keys(obj: Any) -> int:
            if isinstance(obj, dict):
                return len(obj) + sum(count_keys(v) for v in obj.values())
            if isinstance(obj, list):
                return sum(count_keys(v) for v in obj)
            return 0

        keys = count_keys(data)
        summary = f"Interactive JSON explorer: {keys} keys"
        if isinstance(data, list):
            summary += f", {len(data)} top-level items"
        elif isinstance(data, dict):
            summary += f", {len(data)} top-level keys"

        return ToolResult(
            content=[{"type": "text", "text": summary}],
            is_error=False,
        )


# ---------------------------------------------------------------------------
# Color Palette
# ---------------------------------------------------------------------------


class ColorPaletteTool(McpAppTool):
    """Generate and explore colour palettes with harmonies & contrast info."""

    ui_resource_uri: ClassVar[str] = "ui://color_palette"
    risk: ClassVar[ToolRisk] = ToolRisk.SAFE  # read-only generation

    def __init__(self) -> None:
        super().__init__(
            name="color_palette",
            description=(
                "Display an interactive color palette. Provide colors as "
                "an array of hex strings (e.g. ['#FF5733', '#33FF57']) or "
                "objects with {hex, name}. The user can click colors to see "
                "RGB/HSL values, WCAG contrast ratios, and color harmonies."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Palette name or theme",
                    },
                    "colors": {
                        "type": "array",
                        "description": "Array of hex color strings or {hex, name} objects",
                        "items": {"type": "string"},
                    },
                },
                "required": ["colors"],
                "additionalProperties": False,
            },
            annotations={
                "readOnlyHint": True,
                "openWorldHint": False,
                "title": "Color Palette",
            },
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        colors: List[Any],
        title: str = "Palette",
    ) -> ToolResult:

        if not colors:
            return ToolResult(
                content=[{"type": "text", "text": "No colors provided."}],
                is_error=True,
            )

        hex_list = []
        for c in colors:
            if isinstance(c, str):
                hex_list.append(c)
            elif isinstance(c, dict):
                hex_list.append(c.get("hex", c.get("color", "?")))

        summary = f"**{title}** — {len(hex_list)} colors: {', '.join(hex_list[:8])}"
        if len(hex_list) > 8:
            summary += f" … +{len(hex_list) - 8} more"

        return ToolResult(
            content=[{"type": "text", "text": summary}],
            is_error=False,
        )


# ---------------------------------------------------------------------------
# Kanban Board
# ---------------------------------------------------------------------------


class KanbanBoardTool(McpAppTool):
    """Render a drag-and-drop Kanban board for task management."""

    ui_resource_uri: ClassVar[str] = "ui://kanban_board"
    risk: ClassVar[ToolRisk] = ToolRisk.CRITICAL  # writes persistent task data

    def __init__(self) -> None:
        super().__init__(
            name="kanban_board",
            description=(
                "Display an interactive Kanban board with columns and task cards. "
                "The user can drag tasks between columns. Provide `columns` as "
                "a list of column names (e.g. ['To Do', 'In Progress', 'Done']) "
                "and `tasks` as an array of objects with title, column, priority, "
                "description, tags, and assignee."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Board title",
                    },
                    "columns": {
                        "type": "array",
                        "description": "Column names or objects with {id, name, color}",
                        "items": {"type": "string"},
                    },
                    "tasks": {
                        "type": "array",
                        "description": (
                            "Array of task objects: {title, column, priority?, "
                            "description?, tags?, assignee?}"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "column": {"type": "string"},
                                "priority": {"type": "string"},
                                "description": {"type": "string"},
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "assignee": {"type": "string"},
                            },
                            "required": ["title", "column"],
                        },
                    },
                },
                "required": ["columns", "tasks"],
                "additionalProperties": False,
            },
            annotations={
                "readOnlyHint": False,
                "openWorldHint": False,
                "title": "Kanban Board",
            },
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        columns: List[Any],
        tasks: List[Any],
        title: str = "Kanban Board",
    ) -> ToolResult:

        col_names = []
        for c in columns:
            if isinstance(c, str):
                col_names.append(c)
            elif isinstance(c, dict):
                col_names.append(c.get("name", c.get("title", "?")))

        summary = f"**{title}**\nColumns: {', '.join(col_names)} | {len(tasks)} tasks"

        return ToolResult(
            content=[{"type": "text", "text": summary}],
            is_error=False,
        )


# ---------------------------------------------------------------------------
# Spotify Player
# ---------------------------------------------------------------------------


class SpotifyPlayerTool(McpAppTool):
    """Search Spotify and display an interactive music player with Web Playback SDK.

    Uses Spotify Web Playback SDK to play FULL TRACKS (not just previews).
    Requires user to log in with Spotify Premium account.
    """

    ui_resource_uri: ClassVar[str] = "ui://spotify_player_sdk"
    risk: ClassVar[ToolRisk] = (
        ToolRisk.CRITICAL
    )  # external service, acts on behalf of user

    def __init__(self, spotify_service: Any = None) -> None:
        self._spotify = spotify_service
        self._base_spotify_service = spotify_service  # Keep base service for refreshing
        super().__init__(
            name="spotify_player",
            description=(
                "Search Spotify for music and display an interactive player with "
                "full track playback using Web Playback SDK. Users can log in with "
                "their Spotify Premium account to play complete songs (not just "
                "30-second previews). Features: play/pause, skip tracks, shuffle, "
                "repeat, volume control, and see album art. Provide a search query "
                "(song name, artist, genre, mood, etc.). You can also specify a genre hint."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query — can be a song name, artist, genre, "
                            "mood, or any music-related phrase. Required for source='search'."
                        ),
                    },
                    "source": {
                        "type": "string",
                        "description": "The source to play from. 'search' (default) uses the query, 'liked_songs' plays your saved tracks, 'playlists' lists your playlists.",
                        "enum": ["search", "liked_songs", "playlists"],
                        "default": "search",
                    },
                    "genre": {
                        "type": "string",
                        "description": "Optional genre hint (e.g. jazz, rock, classical, hip-hop)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of tracks to return (default: 20, max: 50)",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            annotations={
                "readOnlyHint": False,
                "openWorldHint": True,
                "title": "Spotify Player (SDK)",
            },
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        query: str = "",
        source: str = "search",
        genre: str = "",
        limit: int = 20,
    ) -> ToolResult:

        # Check if Spotify service is configured
        if not self._base_spotify_service:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": (
                            "Spotify API not configured. Set SPOTIFY_CLIENT_ID and "
                            "SPOTIFY_CLIENT_SECRET environment variables."
                        ),
                    }
                ],
                is_error=True,
            )

        # Try to use OAuth token from Next.js API if user is authenticated
        oauth_token = None
        try:
            url = settings.FRONTEND_URL.rstrip("/") + _SPOTIFY_TOKEN_PATH
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                if resp.is_success:
                    data = resp.json()
                    if data.get("access_token"):
                        oauth_token = data["access_token"]
                        logger.info(
                            "Using OAuth token from Next.js for Spotify operation"
                        )
        except Exception as e:
            logger.debug("No OAuth token available from Next.js: %s", e)

        from ravi.adapters.spotify.client import SpotifyService

        if oauth_token:
            spotify = SpotifyService(
                client_id=self._base_spotify_service._client_id,
                client_secret=self._base_spotify_service._client_secret,
                oauth_token=oauth_token,
            )
        else:
            spotify = self._base_spotify_service

        effective_limit = min(limit, 50)
        tracks: list = []
        app_data: Dict[str, Any] = {"source": source}

        try:
            if source == "liked_songs":
                result = await spotify.get_liked_songs(limit=effective_limit)
                tracks = result.get("tracks", [])
                app_data["tracks"] = tracks
                summary_prefix = f"Fetched {len(tracks)} liked songs from your library."
            elif source == "playlists":
                result = await spotify.get_playlists(limit=effective_limit)
                playlists = result.get("playlists", [])
                app_data["playlists"] = playlists
                summary_prefix = f"Found {len(playlists)} playlists in your account."
                return ToolResult(
                    content=[{"type": "text", "text": summary_prefix}],
                    is_error=False,
                    app_data=app_data,
                )
            else:
                # Default: search
                if not query:
                    return ToolResult(
                        content=[
                            {
                                "type": "text",
                                "text": "Search query is required for search source.",
                            }
                        ],
                        is_error=True,
                    )
                tracks = await spotify.search_tracks(
                    query=query,
                    limit=effective_limit,
                )
                app_data["tracks"] = tracks
                app_data["query"] = query
                summary_prefix = f'Found {len(tracks)} tracks for "{query}"'

        except Exception as e:
            logger.warning(
                "Spotify %s failed: %s",
                source,
                e,
            )
            return ToolResult(
                content=[{"type": "text", "text": f"Spotify {source} failed: {e}"}],
                is_error=True,
            )

        if not tracks and source != "playlists":
            return ToolResult(
                content=[{"type": "text", "text": f"No tracks found for {source}."}],
                is_error=False,
            )

        # Check Spotify OAuth authentication status
        is_authenticated = await _is_spotify_authenticated_async()

        # Build text summary for the LLM
        track_list = []
        for i, t in enumerate(tracks[:10], 1):
            track_list.append(f"{i}. 🎵 {t['name']} — {t['artist']} ({t['album']})")

        # Different message based on authentication state
        if is_authenticated:
            summary = (
                summary_prefix
                + ". User is connected to Spotify Premium and can play full tracks.\n"
                + "\n".join(track_list)
            )
        else:
            summary = (
                summary_prefix
                + ". ⚠️ User needs to connect their Spotify Premium account first to play full tracks.\n"
                + "\n".join(track_list)
            )

        return ToolResult(
            content=[{"type": "text", "text": summary}],
            is_error=False,
            app_data=app_data,
        )


# ── Google Workspace Tool ────────────────────────────────────────────────────


def _workspace_query_matches(query: str, *values: str) -> bool:
    if not query:
        return True

    needle = query.strip().lower()
    return any(needle in value.lower() for value in values if value)


def _format_google_datetime(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value

    return dt.astimezone().strftime("%a, %b %d %I:%M %p")


def _format_google_date(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value

    return dt.strftime("%a, %b %d")


def _extract_google_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        if isinstance(error, str) and error:
            return error

    return response.text[:200] or response.reason_phrase or "unknown error"


async def _workspace_get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Any = None,
) -> Dict[str, Any]:
    response = await client.get(url, params=params)
    if not response.is_success:
        raise RuntimeError(
            f"Google API {response.status_code}: {_extract_google_error(response)}"
        )
    return response.json()


def _gmail_header_map(headers: List[Dict[str, Any]]) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for header in headers:
        name = header.get("name")
        value = header.get("value")
        if isinstance(name, str) and isinstance(value, str):
            parsed[name.lower()] = value
    return parsed


def _sender_name(from_header: str) -> str:
    if "<" in from_header:
        prefix = from_header.split("<", 1)[0].strip()
        if prefix:
            return prefix.strip('"')
    return from_header.strip() or "Unknown"


def _format_email_date(date_header: str) -> str:
    try:
        return parsedate_to_datetime(date_header).astimezone().strftime("%a, %b %d")
    except (TypeError, ValueError, IndexError):
        return date_header


async def _build_calendar_summary(
    client: httpx.AsyncClient,
    *,
    query: str,
) -> str:
    now = datetime.now(timezone.utc)
    data = await _workspace_get_json(
        client,
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        params={
            "timeMin": now.isoformat(),
            "timeMax": (now + timedelta(days=7)).isoformat(),
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 10,
        },
    )

    lines: List[str] = []
    for event in data.get("items", []):
        start = event.get("start", {}) or {}
        summary = str(event.get("summary") or "(No title)")
        location = str(event.get("location") or "")
        description = str(event.get("description") or "")

        if not _workspace_query_matches(query, summary, location, description):
            continue

        if isinstance(start.get("dateTime"), str):
            when = _format_google_datetime(start["dateTime"])
        elif isinstance(start.get("date"), str):
            when = _format_google_date(start["date"]) + " (all day)"
        else:
            when = "Unknown time"

        line = f"- {when}: {summary}"
        if location:
            line += f" ({location})"
        lines.append(line)

    if not lines:
        if query:
            return f'No upcoming calendar events matched "{query}" in the next 7 days.'
        return "No upcoming calendar events in the next 7 days."

    heading = "Upcoming calendar events in the next 7 days"
    if query:
        heading += f' matching "{query}"'
    return heading + ":\n" + "\n".join(lines)


async def _build_gmail_summary(
    client: httpx.AsyncClient,
    *,
    query: str,
) -> str:
    list_data = await _workspace_get_json(
        client,
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        params={"labelIds": "INBOX", "maxResults": 10},
    )
    message_refs = list_data.get("messages", [])

    if not message_refs:
        return "Inbox is empty."

    detail_requests = [
        _workspace_get_json(
            client,
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message['id']}",
            params=[
                ("format", "metadata"),
                ("metadataHeaders", "From"),
                ("metadataHeaders", "Subject"),
                ("metadataHeaders", "Date"),
            ],
        )
        for message in message_refs
        if isinstance(message, dict) and isinstance(message.get("id"), str)
    ]
    messages = await asyncio.gather(*detail_requests)

    lines: List[str] = []
    for message in messages:
        payload = message.get("payload", {}) or {}
        headers = _gmail_header_map(payload.get("headers", []))
        sender = _sender_name(headers.get("from", "Unknown"))
        subject = headers.get("subject", "(No subject)")
        snippet = str(message.get("snippet") or "")
        date_label = (
            _format_email_date(headers.get("date", "")) if headers.get("date") else ""
        )

        if not _workspace_query_matches(
            query,
            sender,
            subject,
            snippet,
            headers.get("from", ""),
        ):
            continue

        line = f"- {sender}: {subject}"
        if date_label:
            line += f" [{date_label}]"
        lines.append(line)

    if not lines:
        if query:
            return f'No recent inbox messages matched "{query}".'
        return "No recent inbox messages found."

    heading = "Recent inbox messages"
    if query:
        heading += f' matching "{query}"'
    return heading + ":\n" + "\n".join(lines)


async def _build_drive_summary(
    client: httpx.AsyncClient,
    *,
    query: str,
) -> str:
    data = await _workspace_get_json(
        client,
        "https://www.googleapis.com/drive/v3/files",
        params={
            "orderBy": "viewedByMeTime desc",
            "pageSize": 10,
            "q": "trashed = false",
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink,owners(displayName))",
        },
    )

    lines: List[str] = []
    for file_meta in data.get("files", []):
        name = str(file_meta.get("name") or "Untitled")
        owners = file_meta.get("owners", [])
        owner = ""
        if isinstance(owners, list) and owners:
            first_owner = owners[0] or {}
            if isinstance(first_owner, dict):
                owner = str(first_owner.get("displayName") or "")
        modified = str(file_meta.get("modifiedTime") or "")

        if not _workspace_query_matches(query, name, owner):
            continue

        line = f"- {name}"
        meta_bits = [
            bit
            for bit in [owner, _format_google_datetime(modified) if modified else ""]
            if bit
        ]
        if meta_bits:
            line += " — " + " · ".join(meta_bits)
        lines.append(line)

    if not lines:
        if query:
            return f'No recent Drive files matched "{query}".'
        return "No recent Drive files found."

    heading = "Recent Drive files"
    if query:
        heading += f' matching "{query}"'
    return heading + ":\n" + "\n".join(lines)


async def _is_workspace_connected_async(redis: Any) -> bool:
    """Return True if the engine currently has a workspace token in Redis."""
    return bool(await get_workspace_access_token_async(redis))


async def _create_calendar_event(
    client: httpx.AsyncClient,
    *,
    title: str,
    start_time: str,
    end_time: str,
    location: str = "",
    description: str = "",
) -> str:
    """POST a new event to Google Calendar primary calendar."""
    if not title:
        raise ValueError("title is required to create an event")
    if not start_time:
        raise ValueError("start_time is required to create an event")

    # Default end_time to 1 hour after start_time if not provided
    if not end_time:
        try:
            dt = datetime.fromisoformat(start_time)
            end_time = (dt + timedelta(hours=1)).isoformat()
        except ValueError:
            end_time = start_time

    body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": start_time},
        "end": {"dateTime": end_time},
    }
    if location:
        body["location"] = location
    if description:
        body["description"] = description

    resp = await client.post(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        json=body,
    )
    if not resp.is_success:
        raise RuntimeError(
            f"Google API {resp.status_code}: {_extract_google_error(resp)}"
        )

    data = resp.json()
    event_id = data.get("id", "")
    event_link = data.get("htmlLink", "")
    result = f"✅ Created event: {title} starting {_format_google_datetime(start_time)}"
    if event_id:
        result += f"\nEvent ID: {event_id}"
    if event_link:
        result += f"\n🔗 {event_link}"
    return result


async def _cancel_calendar_event(
    client: httpx.AsyncClient,
    *,
    event_id: str,
) -> str:
    """DELETE a Google Calendar event by ID."""
    if not event_id:
        raise ValueError("event_id is required to cancel an event")

    resp = await client.delete(
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
    )
    if resp.status_code == 404:
        raise RuntimeError("Event not found. It may have already been deleted.")
    if not resp.is_success and resp.status_code != 204:
        raise RuntimeError(
            f"Google API {resp.status_code}: {_extract_google_error(resp)}"
        )
    return "✅ Calendar event cancelled successfully."


class GoogleWorkspaceTool(McpAppTool):
    """Read and write Google Workspace data and open an interactive panel."""

    ui_resource_uri: ClassVar[str] = "ui://google_workspace"
    risk: ClassVar[ToolRisk] = ToolRisk.SENSITIVE

    def __init__(self, *, redis_client: Any = None) -> None:
        self._redis = redis_client
        super().__init__(
            name="google_workspace",
            description=(
                "Use this tool whenever the user asks about their Gmail messages, "
                "Calendar events, or Drive files — or wants to create or cancel a "
                "calendar event. It returns a concise summary from the live Google APIs "
                "and opens the interactive Google Workspace panel. "
                "Requires the user to connect Google Workspace in Settings > Apps. "
                "For 'action=create_event' provide title, start_time (ISO 8601 with "
                "timezone e.g. '2026-04-20T19:00:00+05:30'), and optionally end_time "
                "(defaults to 1 hour after start). "
                "For 'action=cancel_event' provide event_id."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "enum": ["drive", "calendar", "gmail"],
                        "description": "Which Google service to inspect and open first (default: drive). For write actions, calendar is used automatically.",
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Filter query for messages, events, or files. "
                            "Pass an empty string when no filter is needed."
                        ),
                    },
                    "action": {
                        "type": "string",
                        "enum": ["read", "create_event", "cancel_event"],
                        "description": "Operation to perform: 'read' (default) lists data, 'create_event' creates a calendar event, 'cancel_event' deletes an event.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Event title — required for create_event.",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Event start as ISO 8601 string with timezone, e.g. '2026-04-20T19:00:00+05:30'. Required for create_event.",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "Event end as ISO 8601 string with timezone. Defaults to 1 hour after start_time if omitted.",
                    },
                    "event_id": {
                        "type": "string",
                        "description": "Google Calendar event ID — required for cancel_event.",
                    },
                    "location": {
                        "type": "string",
                        "description": "Event location (optional, for create_event).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Event description / notes (optional, for create_event).",
                    },
                },
                "additionalProperties": False,
            },
            annotations={
                "readOnlyHint": False,
                "openWorldHint": True,
                "title": "Google Workspace",
            },
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        service: str = "drive",
        query: str = "",
        action: str = "read",
        title: str = "",
        start_time: str = "",
        end_time: str = "",
        event_id: str = "",
        location: str = "",
        description: str = "",
    ) -> ToolResult:
        service_name = service if service in {"drive", "calendar", "gmail"} else "drive"
        if action in ("create_event", "cancel_event"):
            service_name = "calendar"
        if self._redis is None:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "Google Workspace tool is not configured (no Redis client).",
                    }
                ],
                is_error=True,
                app_data={"service": service_name, "query": query, "connected": False},
            )
        connected = await _is_workspace_connected_async(self._redis)

        if not connected:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": (
                            "Google Workspace is not connected. "
                            "Please go to Settings → Apps and click 'Connect Google Workspace' "
                            "to grant access to Drive, Calendar, and Gmail."
                        ),
                    }
                ],
                is_error=False,
                app_data={"service": service_name, "query": query, "connected": False},
            )

        access_token = await get_workspace_access_token_async(self._redis)
        if not access_token:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": (
                            "Google Workspace was connected earlier, but the backend no "
                            "longer has a usable access token. Reconnect in Settings → Apps."
                        ),
                    }
                ],
                is_error=True,
                app_data={"service": service_name, "query": query, "connected": False},
            )

        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                headers={"Authorization": f"Bearer {access_token}"},
            ) as client:
                if action == "create_event":
                    summary = await _create_calendar_event(
                        client,
                        title=title,
                        start_time=start_time,
                        end_time=end_time,
                        location=location,
                        description=description,
                    )
                elif action == "cancel_event":
                    summary = await _cancel_calendar_event(client, event_id=event_id)
                elif service_name == "calendar":
                    summary = await _build_calendar_summary(client, query=query)
                elif service_name == "gmail":
                    summary = await _build_gmail_summary(client, query=query)
                else:
                    summary = await _build_drive_summary(client, query=query)
        except Exception as exc:
            logger.warning(
                "Google Workspace %s lookup failed (query=%r): %s",
                service_name,
                query,
                exc,
            )
            if "Google API 401:" in str(exc):
                await clear_workspace_tokens_async(self._redis)
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": (
                                "Google Workspace authentication expired or was rejected by "
                                "Google. Open Settings → Apps once to refresh the token, "
                                "then try the request again."
                            ),
                        }
                    ],
                    is_error=True,
                    app_data={
                        "service": service_name,
                        "query": query,
                        "connected": False,
                    },
                )
            return ToolResult(
                content=[{"type": "text", "text": str(exc)}],
                is_error=True,
                app_data={"service": service_name, "query": query, "connected": True},
            )

        calendar_mutated = action in ("create_event", "cancel_event")
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": summary,
                }
            ],
            is_error=False,
            app_data={
                "service": service_name,
                "query": query,
                "connected": True,
                **(
                    {
                        "calendar_mutated": True,
                        "action": action,
                    }
                    if calendar_mutated
                    else {}
                ),
            },
        )

"""MCP App-enabled tools with interactive UIs.

These tools declare ``_meta.ui.resourceUri`` so the frontend renders
a sandboxed iframe with the interactive HTML app alongside their output.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from agent_framework.tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Visualizer
# ---------------------------------------------------------------------------

class DataVisualizerTool(BaseTool):
    """Visualise structured data as interactive bar / line / pie charts."""

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
            _meta={
                "ui": {
                    "resourceUri": "ui://data_visualizer",
                }
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        data: List[Dict[str, Any]] = kwargs.get("data", [])
        title: str = kwargs.get("title", "Chart")

        if not data:
            return ToolResult(
                content=[{"type": "text", "text": "No data provided."}],
                isError=True,
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
            isError=False,
        )


# ---------------------------------------------------------------------------
# Markdown Previewer
# ---------------------------------------------------------------------------

class MarkdownPreviewerTool(BaseTool):
    """Render markdown content with a live preview / source toggle."""

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
            _meta={
                "ui": {
                    "resourceUri": "ui://markdown_previewer",
                }
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        content: str = kwargs.get("content", "")
        if not content:
            return ToolResult(
                content=[{"type": "text", "text": "No markdown content provided."}],
                isError=True,
            )

        lines = content.strip().split("\n")
        words = content.split()
        summary = (
            f"Rendered markdown preview: {len(lines)} lines, "
            f"{len(words)} words"
        )
        return ToolResult(
            content=[{"type": "text", "text": summary}],
            isError=False,
        )


# ---------------------------------------------------------------------------
# JSON Explorer
# ---------------------------------------------------------------------------

class JsonExplorerTool(BaseTool):
    """Display structured data in an interactive collapsible tree."""

    def __init__(self) -> None:
        super().__init__(
            name="json_explorer",
            description=(
                "Display any structured data as an interactive JSON tree. "
                "The user can expand/collapse nodes, search keys and values, "
                "and copy individual values. Pass `data` as any JSON-serialisable "
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
                        "description": "Any JSON data (object, array, etc.) to explore",
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
            _meta={
                "ui": {
                    "resourceUri": "ui://json_explorer",
                }
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        data = kwargs.get("data")
        if data is None:
            return ToolResult(
                content=[{"type": "text", "text": "No data provided."}],
                isError=True,
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
            isError=False,
        )


# ---------------------------------------------------------------------------
# Color Palette
# ---------------------------------------------------------------------------

class ColorPaletteTool(BaseTool):
    """Generate and explore colour palettes with harmonies & contrast info."""

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
                        "items": {},
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
            _meta={
                "ui": {
                    "resourceUri": "ui://color_palette",
                }
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        colors = kwargs.get("colors", [])
        title = kwargs.get("title", "Palette")

        if not colors:
            return ToolResult(
                content=[{"type": "text", "text": "No colors provided."}],
                isError=True,
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
            isError=False,
        )


# ---------------------------------------------------------------------------
# Kanban Board
# ---------------------------------------------------------------------------

class KanbanBoardTool(BaseTool):
    """Render a drag-and-drop Kanban board for task management."""

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
                        "items": {},
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
            _meta={
                "ui": {
                    "resourceUri": "ui://kanban_board",
                }
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        columns = kwargs.get("columns", [])
        tasks = kwargs.get("tasks", [])
        title = kwargs.get("title", "Kanban Board")

        col_names = []
        for c in columns:
            if isinstance(c, str):
                col_names.append(c)
            elif isinstance(c, dict):
                col_names.append(c.get("name", c.get("title", "?")))

        summary = (
            f"**{title}**\n"
            f"Columns: {', '.join(col_names)} | {len(tasks)} tasks"
        )

        return ToolResult(
            content=[{"type": "text", "text": summary}],
            isError=False,
        )


# ---------------------------------------------------------------------------
# Spotify Player
# ---------------------------------------------------------------------------

class SpotifyPlayerTool(BaseTool):
    """Search Spotify and display an interactive music player with Web Playback SDK.
    
    Uses Spotify Web Playback SDK to play FULL TRACKS (not just previews).
    Requires user to log in with Spotify Premium account.
    """

    def __init__(self, spotify_service: Any = None) -> None:
        self._spotify = spotify_service
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
                            "mood, or any music-related phrase"
                        ),
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
                "required": ["query"],
                "additionalProperties": False,
            },
            annotations={
                "readOnlyHint": False,
                "openWorldHint": True,
                "title": "Spotify Player (SDK)",
            },
            _meta={
                "ui": {
                    "resourceUri": "ui://spotify_player_sdk",
                }
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        query: str = kwargs.get("query", "")
        genre: str = kwargs.get("genre", "")
        limit: int = kwargs.get("limit", 20)

        if not query:
            return ToolResult(
                content=[{"type": "text", "text": "No search query provided."}],
                isError=True,
            )

        # Check if Spotify service is configured
        if not self._spotify:
            return ToolResult(
                content=[{
                    "type": "text",
                    "text": (
                        "Spotify API not configured. Set SPOTIFY_CLIENT_ID and "
                        "SPOTIFY_CLIENT_SECRET environment variables."
                    ),
                }],
                isError=True,
            )

        # Build search query with optional genre
        search_query = query
        if genre:
            search_query = f"{query} genre:{genre}"

        try:
            tracks = await self._spotify.search_tracks(
                query=search_query,
                limit=min(limit, 50),
            )
        except Exception as e:
            logger.exception("Spotify search failed")
            return ToolResult(
                content=[{"type": "text", "text": f"Spotify search failed: {e}"}],
                isError=True,
            )

        if not tracks:
            return ToolResult(
                content=[{"type": "text", "text": f"No tracks found for '{query}'."}],
                isError=False,
            )

        # Filter to tracks with preview URLs available
        playable = [t for t in tracks if t.get("preview_url")]
        all_tracks = tracks

        # Build text summary for the LLM
        track_list = []
        for i, t in enumerate(all_tracks[:10], 1):
            track_list.append(
                f"{i}. 🎵 {t['name']} — {t['artist']} "
                f"({t['album']})"
            )

        summary = (
            f"Found {len(all_tracks)} tracks for \"{query}\""
            + (f" (genre: {genre})" if genre else "")
            + f". User can log in with Spotify Premium to play full tracks.\n"
            + "\n".join(track_list)
        )

        return ToolResult(
            content=[{"type": "text", "text": summary}],
            isError=False,
            app_data={
                "tracks": all_tracks,
                "query": query,
                "genre": genre,
            },
        )

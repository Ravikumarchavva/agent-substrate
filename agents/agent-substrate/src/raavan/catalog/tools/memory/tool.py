"""MemoryTool — persistent agent notes across conversations.

Provides the agent with a simple key-value scratchpad backed by Redis.
Notes survive across conversation turns and can be recalled later.
"""

from __future__ import annotations

from typing import Any

from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk


class MemoryTool(BaseTool):
    """Read/write persistent notes via Redis."""

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
        super().__init__(
            name="memory_tool",
            description=(
                "Store, retrieve, list, or delete persistent notes. "
                "Notes are key-value pairs that survive across conversations."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["save", "recall", "list", "delete"],
                        "description": "Action: save a note, recall by key, list all, or delete a key",
                    },
                    "key": {
                        "type": "string",
                        "description": "Note key / identifier (required for save, recall, delete)",
                    },
                    "value": {
                        "type": "string",
                        "description": "Content to store (required for save action)",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            risk=ToolRisk.SAFE,
            category="productivity",
            tags=["memory", "note", "remember", "store", "recall", "persist"],
            aliases=["notes", "remember"],
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        action: str,
        key: str = "",
        value: str = "",
    ) -> ToolResult:
        if self._redis is None:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "Memory tool is not configured (no Redis client).",
                    }
                ],
                is_error=True,
            )

        prefix = "agent_memory:"

        if action == "save":
            if not key.strip() or not value.strip():
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": "Both 'key' and 'value' are required for save.",
                        }
                    ],
                    is_error=True,
                )
            await self._redis.set(f"{prefix}{key}", value)
            return ToolResult(
                content=[{"type": "text", "text": f"Saved note '{key}'."}],
            )

        if action == "recall":
            if not key.strip():
                return ToolResult(
                    content=[{"type": "text", "text": "'key' is required for recall."}],
                    is_error=True,
                )
            stored = await self._redis.get(f"{prefix}{key}")
            if stored is None:
                return ToolResult(
                    content=[
                        {"type": "text", "text": f"No note found for key '{key}'."}
                    ],
                )
            text = stored.decode() if isinstance(stored, bytes) else str(stored)
            return ToolResult(
                content=[{"type": "text", "text": f"{key}: {text}"}],
            )

        if action == "list":
            keys = []
            async for k in self._redis.scan_iter(match=f"{prefix}*"):
                name = k.decode() if isinstance(k, bytes) else str(k)
                keys.append(name.removeprefix(prefix))
            if not keys:
                return ToolResult(
                    content=[{"type": "text", "text": "No notes stored."}],
                )
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": f"Stored notes ({len(keys)}): {', '.join(sorted(keys))}",
                    }
                ],
            )

        if action == "delete":
            if not key.strip():
                return ToolResult(
                    content=[{"type": "text", "text": "'key' is required for delete."}],
                    is_error=True,
                )
            deleted = await self._redis.delete(f"{prefix}{key}")
            if deleted:
                return ToolResult(
                    content=[{"type": "text", "text": f"Deleted note '{key}'."}],
                )
            return ToolResult(
                content=[{"type": "text", "text": f"Note '{key}' not found."}],
            )

        return ToolResult(
            content=[{"type": "text", "text": f"Unknown action: {action!r}"}],
            is_error=True,
        )

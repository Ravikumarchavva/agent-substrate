"""MemoryTool — persistent agent notes across conversations.

Provides the agent with a simple key-value scratchpad backed by Redis.
Notes survive across conversation turns and can be recalled later.
"""

from __future__ import annotations

from typing import Any

from ravi.kernel.tools import ToolExecutionResult
from ravi.kernel import TextBlock


class MemoryTool:
    """Read/write persistent notes via Redis."""

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
async def execute(  # type: ignore[override]
        self,
        *,
        action: str,
        key: str = "",
        value: str = "",
    ) -> ToolExecutionResult:
        if self._redis is None:
            return ToolExecutionResult(
                content=[
                    TextBlock(text="Memory tool is not configured (no Redis client).")
                ],
                is_error=True,
            )

        prefix = "agent_memory:"

        if action == "save":
            if not key.strip() or not value.strip():
                return ToolExecutionResult(
                    content=[
                        TextBlock(text="Both 'key' and 'value' are required for save.")
                    ],
                    is_error=True,
                )
            await self._redis.set(f"{prefix}{key}", value)
            return ToolExecutionResult(
                content=[TextBlock(text=f"Saved note '{key}'.")],
            )

        if action == "recall":
            if not key.strip():
                return ToolExecutionResult(
                    content=[TextBlock(text="'key' is required for recall.")],
                    is_error=True,
                )
            stored = await self._redis.get(f"{prefix}{key}")
            if stored is None:
                return ToolExecutionResult(
                    content=[TextBlock(text=f"No note found for key '{key}'.")],
                )
            text = stored.decode() if isinstance(stored, bytes) else str(stored)
            return ToolExecutionResult(
                content=[TextBlock(text=f"{key}: {text}")],
            )

        if action == "list":
            keys = []
            async for k in self._redis.scan_iter(match=f"{prefix}*"):
                name = k.decode() if isinstance(k, bytes) else str(k)
                keys.append(name.removeprefix(prefix))
            if not keys:
                return ToolExecutionResult(
                    content=[TextBlock(text="No notes stored.")],
                )
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=f"Stored notes ({len(keys)}): {', '.join(sorted(keys))}"
                    ),
                ],
            )

        if action == "delete":
            if not key.strip():
                return ToolExecutionResult(
                    content=[TextBlock(text="'key' is required for delete.")],
                    is_error=True,
                )
            deleted = await self._redis.delete(f"{prefix}{key}")
            if deleted:
                return ToolExecutionResult(
                    content=[TextBlock(text=f"Deleted note '{key}'.")],
                )
            return ToolExecutionResult(
                content=[TextBlock(text=f"Note '{key}' not found.")],
            )

        return ToolExecutionResult(
            content=[TextBlock(text=f"Unknown action: {action!r}")],
            is_error=True,
        )

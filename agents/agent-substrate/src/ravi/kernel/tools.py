"""Tool execution contracts.

Minimal kernel-level types: what an agent requests to run, and what comes back.
Execution policy (risk rating, timeout, human-in-the-loop approval) is a
fabric/guardrail concern and lives above this layer.
"""

from __future__ import annotations

from uuid import uuid4

from typing import Protocol

from pydantic import BaseModel, Field

from ravi.kernel.content import ContentBlock, JsonObject, content_blocks_to_str


class ToolCallRequest(BaseModel):
    """A request to execute a named tool."""

    name: str
    arguments: JsonObject = Field(default_factory=dict)
    call_id: str = Field(default_factory=lambda: str(uuid4()))

    model_config = {"frozen": True}


class ToolExecutionResult(BaseModel):
    """Result from a single tool execution."""

    call_id: str = ""
    name: str = ""
    content: list[ContentBlock] = Field(default_factory=list)
    is_error: bool = False
    metadata: JsonObject = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True, "frozen": False}

    @property
    def text(self) -> str:
        """Human-readable lowering of all content blocks."""
        return content_blocks_to_str(self.content)


class Tool(Protocol):
    """Contract every catalog tool must satisfy."""
    name: str
    description: str
    input_schema: dict[str, object]

    async def execute(self, **kwargs: object) -> ToolExecutionResult:
        ...


__all__ = ["ToolCallRequest", "ToolExecutionResult", "Tool"]

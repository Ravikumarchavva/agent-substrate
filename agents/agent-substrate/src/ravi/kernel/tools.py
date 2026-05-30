"""Tool execution contracts."""

from __future__ import annotations

from enum import Enum
from uuid import uuid4

from typing import Protocol

from pydantic import BaseModel, Field

from ravi.kernel.content import ContentBlock, JsonObject, content_blocks_to_str


class ToolRisk(str, Enum):
    """Risk classification for a tool.

    SAFE     — no side-effects; execute without approval.
    HIGH     — external side-effects (email, DB write); require approval when
               an ApprovalHandler is configured.
    CRITICAL — destructive / irreversible; always require approval.
    """

    SAFE = "safe"
    HIGH = "high"
    CRITICAL = "critical"


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
    """Contract every catalog tool must satisfy.

    ``risk`` is optional — defaults to ``ToolRisk.SAFE`` when absent.
    """

    name: str
    description: str
    input_schema: dict[str, object]

    async def execute(self, **kwargs: object) -> ToolExecutionResult: ...


__all__ = ["ToolRisk", "ToolCallRequest", "ToolExecutionResult", "Tool"]

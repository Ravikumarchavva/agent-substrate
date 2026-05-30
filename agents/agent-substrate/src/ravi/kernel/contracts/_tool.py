"""Canonical tool execution contracts.

These are the minimal kernel-level types: what the agent asks to run,
and what comes back. Execution policy (risk, timeout, HITL) is a
fabric/guardrail concern and lives above this layer.
"""

from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, Field

from ravi.kernel.messages.content import ContentBlock, JsonObject, TextBlock


class ToolCallRequest(BaseModel):
    """A request to execute a named tool."""

    name: str
    arguments: JsonObject = Field(default_factory=dict)
    call_id: str = Field(default_factory=lambda: str(uuid4()))

    model_config = {"frozen": True}


class ToolExecutionResult(BaseModel):
    """Result from a single tool execution."""

    call_id: str
    name: str
    content: list[ContentBlock] = Field(default_factory=list)
    is_error: bool = False
    metadata: JsonObject = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True, "frozen": False}

    @property
    def text(self) -> str:
        return " ".join(b.text for b in self.content if isinstance(b, TextBlock))

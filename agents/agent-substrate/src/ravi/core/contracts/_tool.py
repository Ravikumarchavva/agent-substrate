"""Canonical tool execution contracts for the core runtime.

These are ENGINE-INTERNAL types. For cross-service HTTP contracts use
``shared.contracts.tool``.
"""

from __future__ import annotations

from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from ravi.core.messages.content import ContentBlock, JsonObject, TextBlock
from ravi.core.tools.base_tool import ToolRisk


class ToolCallRequest(BaseModel):
    """Typed request to execute a tool inside the agent runtime loop.

    Produced by the agent when the LLM requests a tool call.
    Consumed by ``ToolExecutor`` (Sprint 4).

    Fields carry everything the executor needs without passing 22 keyword
    arguments down the call stack.
    """

    name: str
    arguments: JsonObject = Field(default_factory=dict)
    call_id: str = Field(default_factory=lambda: str(uuid4()))

    # Execution context — for tracing, HITL gating, and logging
    agent_name: str = ""
    run_id: str = ""
    step: int = 0

    # Risk policy — drives HITL and saga protection
    risk: ToolRisk = ToolRisk.SAFE
    timeout_seconds: Optional[float] = None

    model_config = {"frozen": True}


class ToolExecutionResult(BaseModel):
    """Canonical result from a single tool execution.

    Produced by ``ToolExecutor`` (Sprint 4).
    Consumed by the agent loop to build:
      - ``ToolExecutionResultMessage`` → memory
      - ``ToolCallRecord``           → audit trail in ``AgentRunResult``
    """

    call_id: str
    name: str
    content: list[ContentBlock] = Field(default_factory=list)
    is_error: bool = False
    duration_ms: Optional[float] = None
    metadata: JsonObject = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True, "frozen": False}

    @property
    def text(self) -> str:
        """Concatenate text blocks; returns empty string if none."""
        return " ".join(b.text for b in self.content if isinstance(b, TextBlock))

"""Middleware context types and agent result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ravi.kernel import ChatMessage, Tool
from ravi.kernel.llm import LLMResponse
from ravi.kernel.message import ToolExecutionResult


# ---------------------------------------------------------------------------
# Agent run result types (defined here to avoid circular imports with react.py)
# ---------------------------------------------------------------------------


@dataclass
class ToolCallRecord:
    name: str
    call_id: str
    arguments: dict[str, Any]
    result: str
    is_error: bool
    duration_ms: float


@dataclass
class AgentRunResult:
    """Result of a completed agent run."""

    output: str
    status: str  # "success" | "error" | "max_iterations" | "paused"
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    run_id: str = ""
    error: str | None = None

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "output": self.output,
            "status": self.status,
            "run_id": self.run_id,
            "error": self.error,
            "tool_calls": [
                {
                    "name": r.name,
                    "call_id": r.call_id,
                    "result": r.result,
                    "is_error": r.is_error,
                    "duration_ms": r.duration_ms,
                }
                for r in self.tool_calls
            ],
        }


# ---------------------------------------------------------------------------
# Middleware context types
# ---------------------------------------------------------------------------


@dataclass
class AgentRunContext:
    agent_name: str
    run_id: str
    session_id: str
    messages: list[ChatMessage]
    result: AgentRunResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatContext:
    agent_name: str
    run_id: str
    messages: list[ChatMessage]
    system_instructions: str
    tools: list[Tool] | None
    result: LLMResponse | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FunctionContext:
    agent_name: str
    run_id: str
    function_name: str
    arguments: dict[str, Any]
    result: ToolExecutionResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

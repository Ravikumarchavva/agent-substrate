"""The one middleware context, plus agent run result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from substrate.kernel import ChatMessage, Tool
from substrate.kernel.agent.middleware import MiddlewareStage
from substrate.kernel.llm import LLMResponse
from substrate.kernel.tools.chain import InvocationResult


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
# The one middleware context
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class MiddlewareContext:
    """The one context shape every middleware in the framework receives.

    ``stage`` says which of the three call sites constructed this instance
    (``react.py``'s ``_handle_message()`` for TURN;
    ``RunContext.llm()``/``.tool()`` for CHAT/TOOL) — fields not meaningful
    for that stage are simply ``None``. Each of the three result fields is
    precisely typed rather than a single loose ``Any``, since the three
    result shapes (``AgentRunResult``/``LLMResponse``/``InvocationResult``)
    are genuinely different classes middleware reads real members off of.
    """

    stage: MiddlewareStage
    agent_name: str
    run_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    # TURN — one agent.run() inbox message
    session_id: str | None = None
    messages: list[ChatMessage] | None = None  # also the CHAT-stage per-call window
    turn_result: AgentRunResult | None = None

    # CHAT — one model.generate() call
    system_instructions: str | None = None
    tools: list[Tool] | None = None
    chat_result: LLMResponse | None = None

    # TOOL — one tool.execute() call
    function_name: str | None = None
    arguments: dict[str, Any] | None = None
    # InvocationResult — the actual wire-form ``RunContext.tool()`` returns
    # (``status``/``text``/``structured``). It's frozen (pydantic
    # ``model_config = {"frozen": True}``), so a middleware that wants to
    # modify it must reassign via
    # ``context.tool_result = context.tool_result.model_copy(update={...})``.
    tool_result: InvocationResult | None = None

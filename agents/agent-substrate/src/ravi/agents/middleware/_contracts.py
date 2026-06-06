"""Local middleware context contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ravi.kernel import ChatMessage, Tool
from ravi.kernel.llm import LLMResponse
from ravi.agents.core.react import AgentRunResult
from ravi.kernel.message import ToolExecutionResult


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
    system: str
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

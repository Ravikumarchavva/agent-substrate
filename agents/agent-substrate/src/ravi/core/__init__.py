"""core - agents, memory, messages, context, guardrails, tools, runtime, middleware."""

from __future__ import annotations

# Canonical contracts
from ravi.core.contracts import (
    ToolCallRequest,
    ToolExecutionResult,
    CanonicalMessage,
    MessageRole,
    ToolCallSpec,
    EventEnvelope,
)

# Canonical enum re-exports
from ravi.core.tools.base_tool import ToolRisk, HitlMode
from ravi.core.guardrails.base_guardrail import GuardrailType
from ravi.core.agents.agent_result import RunStatus
from ravi.core.pipelines.schema import NodeType, EdgeType
from ravi.core.execution.context import ExecutionContext
from ravi.core.middleware.base import (
    BaseMiddleware,
    MiddlewareContext,
    MiddlewareStage,
)
from ravi.core.middleware.runner import MiddlewarePipeline

# Runtime primitives
from ravi.core.runtime import AgentId, TopicId, AgentRuntime, LocalRuntime

__all__ = [
    # Contracts
    "ToolCallRequest",
    "ToolExecutionResult",
    "CanonicalMessage",
    "MessageRole",
    "ToolCallSpec",
    "EventEnvelope",
    # Enums
    "ToolRisk",
    "HitlMode",
    "GuardrailType",
    "RunStatus",
    "NodeType",
    "EdgeType",
    # Execution
    "ExecutionContext",
    # Middleware
    "BaseMiddleware",
    "MiddlewareContext",
    "MiddlewareStage",
    "MiddlewarePipeline",
    # Runtime
    "AgentId",
    "TopicId",
    "AgentRuntime",
    "LocalRuntime",
]

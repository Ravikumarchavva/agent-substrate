"""core - agents, memory, messages, context, guardrails, tools, runtime, middleware."""

from __future__ import annotations

# Canonical contracts
from ravi.kernel.contracts import (
    CanonicalMessage,
    EventEnvelope,
    LocalityHint,
    MessageRole,
    TemporalSemantics,
    ToolCallRequest,
    ToolCallSpec,
    ToolExecutionResult,
    # Trust (routing / allocation tier)
    TrustLevel,
    TrustSignal,
    TrustContext,
    # Placement contracts
    PlacementScope,
    DataGravityHint,
    PlacementContract,
)

# Event fabric contracts
from ravi.kernel.events import (
    AckRequest,
    ConsumeRequest,
    DurableEventLog,
    EventDeliveryMode,
    EventFabric,
    EventPriority,
    PublishRequest,
    RealtimeFanout,
    SubscribeRequest,
)

# Canonical enum re-exports
from ravi.kernel.tools.base_tool import ToolRisk, HitlMode
from ravi.kernel.guardrails.base_guardrail import GuardrailType
from ravi.kernel.agents.agent_result import RunStatus
from ravi.kernel.pipelines.schema import NodeType, EdgeType
from ravi.kernel.execution.context import ExecutionContext
from ravi.kernel.middleware.base import (
    BaseMiddleware,
    MiddlewareContext,
    MiddlewareStage,
)
from ravi.kernel.middleware.runner import MiddlewarePipeline

# Runtime primitives
from ravi.kernel.runtime import AgentId, TopicId, AgentRuntime, LocalRuntime, PrincipalId, PrincipalKind, DelegationToken, IdentityContext

# Dormant agent lifecycle contracts
from ravi.kernel.runtime import (
    AgentLifecycleState,
    ActivationTrigger,
    ExecutionLease,
    CheckpointRef,
    AgentActivationContract,
    Checkpointable,
    ActivationAware,
)

__all__ = [
    # Contracts
    "ToolCallRequest",
    "ToolExecutionResult",
    "CanonicalMessage",
    "MessageRole",
    "ToolCallSpec",
    "EventEnvelope",
    "TemporalSemantics",
    "LocalityHint",
    # Trust (routing / allocation tier)
    "TrustLevel",
    "TrustSignal",
    "TrustContext",
    # Placement contracts
    "PlacementScope",
    "DataGravityHint",
    "PlacementContract",
    # Event fabric contracts
    "EventDeliveryMode",
    "EventPriority",
    "PublishRequest",
    "ConsumeRequest",
    "AckRequest",
    "SubscribeRequest",
    "DurableEventLog",
    "RealtimeFanout",
    "EventFabric",
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
    "PrincipalId",
    "PrincipalKind",
    "DelegationToken",
    "IdentityContext",
    # Dormant agent lifecycle contracts
    "AgentLifecycleState",
    "ActivationTrigger",
    "ExecutionLease",
    "CheckpointRef",
    "AgentActivationContract",
    "Checkpointable",
    "ActivationAware",
]

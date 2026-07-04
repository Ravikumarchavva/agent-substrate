"""substrate.kernel — frozen contracts layer.

Everything here is a Protocol, pure dataclass, or value type.
No I/O, no concrete implementations, no external dependencies beyond pydantic.
"""

from __future__ import annotations

from substrate.kernel.core.content import (
    JsonObject,
    Role,
    BlockValidationError,
    TextBlock,
    ImageBlock,
    AudioBlock,
    VideoBlock,
    DocumentBlock,
    DataBlock,
    CodeBlock,
    ErrorBlock,
    ToolUseBlock,
    ToolResultBlock,
    ThinkingBlock,
    UIResourceBlock,
    UnknownBlock,
    ChatMessage,
    ContentBlock,
    CONTENT_BLOCK_TYPES,
    register_block_type,
    content_block_from_dict,
    content_blocks_to_str,
)
from substrate.kernel.core.identity import (
    AgentId,
    TopicId,
)
from substrate.kernel.agent.supervision import (
    Supervision,
    HistoryRetention,
    Priority,
    SpawnBudget,
    ExecutionBudget,
)
from substrate.kernel.tools.tools import (
    ToolRisk,
    ToolType,
    ToolExecution,
    ToolUI,
    ToolCallRequest,
    ToolExecutionResult,
    FunctionSpec,
    ProviderSpec,
    ToolSpec,
    spec_of,
    Tool,
    HostedTool,
    ProviderDefinedTool,
    AnyTool,
    is_hosted_tool,
    is_provider_defined_tool,
    ToolRegistry,
)
from substrate.kernel.messaging.message import (
    ChatPayload,
    DataPayload,
    ControlPayload,
    ProgressPayload,
    Payload,
    register_payload_type,
    Message,
    Subscription,
)
from substrate.kernel.tools.skills import Skill
from substrate.kernel.core.usage import Usage
from substrate.kernel.llm.llm import (
    GenerationOptions,
    LLMClient,
    LLMResponse,
    EmbeddingClient,
    EmbeddingResult,
)
from substrate.kernel.storage.history import HistoryProvider
from substrate.kernel.agent.context import CompactionStrategy, AgentContextProtocol
from substrate.kernel.agent.middleware import (
    Middleware,
    MiddlewareStage,
    MiddlewareContextProtocol,
)
from substrate.kernel.core.errors import (
    KernelError,
    AgentNotFoundError,
    HandlerError,
    AgentCrashError,
    BudgetExhaustedError,
    MiddlewareTermination,
    CancellationError,
    ConcurrentAppendError,
    SpawnDenied,
)
from substrate.kernel.messaging.stream import (
    TextDelta,
    ReasoningDelta,
    CompletionEvent,
    StreamDone,
    AgentProgress,
    AgentStep,
)
from substrate.kernel.storage.blob import BlobStore
from substrate.kernel.storage.vector import Document, SearchResult, VectorStore
from substrate.kernel.storage.graph import (
    Entity,
    Relationship,
    SubGraph,
    GraphStore,
    CypherCapable,
)
from substrate.kernel.storage.memory import Memory, ShortTermMemory, LongTermMemory
from substrate.kernel.agent.runtime_context import CancellationToken, RunMeta
from substrate.kernel.messaging.events import (
    Event,
    EventHandler,
    EventPublisher,
    EventSubscriber,
)
from substrate.kernel.tools.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalHandler,
)
from substrate.kernel.tools.chain import (
    ChainPolicy,
    ChainFile,
    InvocationResult,
    ChainCallRecord,
    ChainRunResult,
)
from substrate.kernel.runtime import (
    RunId,
    RunStatus,
    new_run_id,
    RunLogEntry,
    EventLog,
    Effect,
    EffectResult,
    Journal,
    DeadLetterReason,
    DeadLetterEntry,
    Inbox,
    FollowGraph,
    FanoutStrategy,
    Wakeup,
    SignalBus,
    RunRetryPolicy,
    Lease,
    Scheduler,
    RunHandle,
    RunResult,
    Supervisor,
    AgentRunContext,
    Agent,
    AskOutcome,
    RunStatusSummary,
)

__all__ = [
    # Content
    "JsonObject",
    "Role",
    "BlockValidationError",
    "TextBlock",
    "ImageBlock",
    "AudioBlock",
    "VideoBlock",
    "DocumentBlock",
    "DataBlock",
    "CodeBlock",
    "ErrorBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ThinkingBlock",
    "UIResourceBlock",
    "UnknownBlock",
    "ChatMessage",
    "ContentBlock",
    "CONTENT_BLOCK_TYPES",
    "register_block_type",
    "content_block_from_dict",
    "content_blocks_to_str",
    # Identity
    "AgentId",
    "TopicId",
    # Supervision
    "Supervision",
    "HistoryRetention",
    "Priority",
    "SpawnBudget",
    "ExecutionBudget",
    # Tools
    "ToolRisk",
    "ToolType",
    "ToolExecution",
    "ToolUI",
    "ToolCallRequest",
    "ToolExecutionResult",
    "FunctionSpec",
    "ProviderSpec",
    "ToolSpec",
    "spec_of",
    "Tool",
    "HostedTool",
    "ProviderDefinedTool",
    "AnyTool",
    "is_hosted_tool",
    "is_provider_defined_tool",
    "ToolRegistry",
    # Payload types
    "ChatPayload",
    "DataPayload",
    "ControlPayload",
    "ProgressPayload",
    "Payload",
    "register_payload_type",
    # Messaging
    "Message",
    "Subscription",
    # Skills
    "Skill",
    # LLM
    "GenerationOptions",
    "LLMClient",
    "LLMResponse",
    "EmbeddingClient",
    "EmbeddingResult",
    "Usage",
    # History
    "HistoryProvider",
    # Context
    "CompactionStrategy",
    "AgentContextProtocol",
    # Middleware
    "Middleware",
    "MiddlewareStage",
    "MiddlewareContextProtocol",
    # Errors
    "KernelError",
    "AgentNotFoundError",
    "HandlerError",
    "AgentCrashError",
    "BudgetExhaustedError",
    "MiddlewareTermination",
    "CancellationError",
    "ConcurrentAppendError",
    "SpawnDenied",
    # Token stream
    "TextDelta",
    "ReasoningDelta",
    "CompletionEvent",
    "StreamDone",
    # Progress stream
    "AgentProgress",
    "AgentStep",
    # Object / blob store
    "BlobStore",
    # Retrieval / RAG knowledge stores
    "Document",
    "SearchResult",
    "VectorStore",
    "Entity",
    "Relationship",
    "SubGraph",
    "GraphStore",
    "CypherCapable",
    # Memory
    "Memory",
    "ShortTermMemory",
    "LongTermMemory",
    # Execution context
    "CancellationToken",
    "RunMeta",
    # Events (generic pub/sub envelope)
    "Event",
    "EventHandler",
    "EventPublisher",
    "EventSubscriber",
    # HITL
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalHandler",
    # Chain
    "ChainPolicy",
    "ChainFile",
    "InvocationResult",
    "ChainCallRecord",
    "ChainRunResult",
    # Durable runtime contracts
    "RunId",
    "RunStatus",
    "new_run_id",
    "RunLogEntry",
    "EventLog",
    "Effect",
    "EffectResult",
    "Journal",
    "DeadLetterReason",
    "DeadLetterEntry",
    "Inbox",
    "FollowGraph",
    "FanoutStrategy",
    "Wakeup",
    "SignalBus",
    "RunRetryPolicy",
    "Lease",
    "Scheduler",
    "RunHandle",
    "RunResult",
    "Supervisor",
    "AgentRunContext",
    "Agent",
    "AskOutcome",
    "RunStatusSummary",
]

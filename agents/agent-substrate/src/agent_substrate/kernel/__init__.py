"""agent_substrate.kernel — frozen contracts layer.

Everything here is a Protocol, pure dataclass, or value type.
No I/O, no concrete implementations, no external dependencies beyond pydantic.
"""

from __future__ import annotations

from agent_substrate.kernel.core.content import (
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
from agent_substrate.kernel.core.identity import (
    AgentId,
    TopicId,
)
from agent_substrate.kernel.agent.supervision import (
    Supervision,
    HistoryRetention,
    Priority,
    SpawnBudget,
    ExecutionBudget,
)
from agent_substrate.kernel.tools.tools import (
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
from agent_substrate.kernel.messaging.message import (
    ChatPayload,
    DataPayload,
    ControlPayload,
    ProgressPayload,
    Payload,
    register_payload_type,
    Message,
    Subscription,
)
from agent_substrate.kernel.tools.skills import Skill
from agent_substrate.kernel.core.usage import Usage
from agent_substrate.kernel.llm.llm import (
    GenerationOptions,
    LLMClient,
    LLMResponse,
    EmbeddingClient,
    EmbeddingResult,
)
from agent_substrate.kernel.storage.history import HistoryProvider
from agent_substrate.kernel.agent.context import CompactionStrategy, AgentContextProtocol
from agent_substrate.kernel.agent.middleware import (
    Middleware,
    AgentMiddleware,
    ChatMiddleware,
    FunctionMiddleware,
    AgentRunContextProtocol,
    ChatContextProtocol,
    FunctionContextProtocol,
)
from agent_substrate.kernel.core.errors import (
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
from agent_substrate.kernel.messaging.stream import (
    TextDelta,
    ReasoningDelta,
    CompletionEvent,
    StreamDone,
    AgentProgress,
    AgentStep,
)
from agent_substrate.kernel.storage.blob import BlobStore
from agent_substrate.kernel.storage.vector import Document, SearchResult, VectorStore
from agent_substrate.kernel.storage.graph import (
    Entity,
    Relationship,
    SubGraph,
    GraphStore,
    CypherCapable,
)
from agent_substrate.kernel.storage.memory import Memory, ShortTermMemory, LongTermMemory
from agent_substrate.kernel.agent.runtime_context import CancellationToken, RunMeta
from agent_substrate.kernel.messaging.events import (
    Event,
    EventHandler,
    EventPublisher,
    EventSubscriber,
)
from agent_substrate.kernel.tools.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalHandler,
)
from agent_substrate.kernel.tools.chain import (
    ChainPolicy,
    ChainFile,
    InvocationResult,
    ChainCallRecord,
    ChainRunResult,
)
from agent_substrate.kernel.runtime import (
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
    "AgentMiddleware",
    "ChatMiddleware",
    "FunctionMiddleware",
    "AgentRunContextProtocol",
    "ChatContextProtocol",
    "FunctionContextProtocol",
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

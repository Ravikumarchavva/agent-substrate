"""ravi.kernel — frozen contracts layer.

Everything here is a Protocol, pure dataclass, or value type.
No I/O, no concrete implementations, no external dependencies beyond pydantic.
"""

from __future__ import annotations

from ravi.kernel.core.content import (
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
from ravi.kernel.core.identity import (
    AgentId,
    TopicId,
)
from ravi.kernel.agent.supervision import (
    Supervision,
    HistoryRetention,
    Priority,
    SpawnBudget,
    ExecutionBudget,
)
from ravi.kernel.tools.tools import (
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
from ravi.kernel.messaging.message import (
    ChatPayload,
    DataPayload,
    ControlPayload,
    ProgressPayload,
    Payload,
    register_payload_type,
    Message,
    Subscription,
)
from ravi.kernel.tools.skills import Skill
from ravi.kernel.core.usage import Usage
from ravi.kernel.llm.llm import (
    GenerationOptions,
    LLMClient,
    LLMResponse,
    EmbeddingClient,
    EmbeddingResult,
)
from ravi.kernel.storage.history import HistoryProvider
from ravi.kernel.agent.context import CompactionStrategy, AgentContextProtocol
from ravi.kernel.agent.middleware import (
    Middleware,
    AgentMiddleware,
    ChatMiddleware,
    FunctionMiddleware,
    AgentRunContextProtocol,
    ChatContextProtocol,
    FunctionContextProtocol,
)
from ravi.kernel.core.errors import (
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
from ravi.kernel.messaging.stream import (
    TextDelta,
    ReasoningDelta,
    CompletionEvent,
    StreamDone,
    AgentProgress,
    AgentStep,
)
from ravi.kernel.storage.blob import BlobStore
from ravi.kernel.storage.vector import Document, SearchResult, VectorStore
from ravi.kernel.storage.graph import (
    Entity,
    Relationship,
    SubGraph,
    GraphStore,
    CypherCapable,
)
from ravi.kernel.storage.memory import Memory, ShortTermMemory, LongTermMemory
from ravi.kernel.agent.runtime_context import CancellationToken, RunMeta
from ravi.kernel.messaging.events import (
    Event,
    EventHandler,
    EventPublisher,
    EventSubscriber,
)
from ravi.kernel.tools.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalHandler,
)
from ravi.kernel.tools.chain import (
    ChainPolicy,
    ChainFile,
    InvocationResult,
    ChainCallRecord,
    ChainRunResult,
)
from ravi.kernel.runtime import (
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

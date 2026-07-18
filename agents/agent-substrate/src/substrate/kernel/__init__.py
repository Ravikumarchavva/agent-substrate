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
    ReasoningBlock,
    ToolUseBlock,
    ToolResultBlock,
    UnknownBlock,
    ChatMessage,
    ContentBlock,
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
    ToolUI,
    ToolCallRequest,
    ToolExecutionResult,
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
    Payload,
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
from substrate.kernel.agent.middleware import MiddlewareStage
from substrate.kernel.core.errors import (
    KernelError,
    AgentCrashError,
    BudgetExhaustedError,
    MiddlewareTermination,
    CancellationError,
    ConcurrentAppendError,
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
from substrate.kernel.tools.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
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
    EventLogProtocol,
    Effect,
    EffectResult,
    DeadLetterReason,
    DeadLetterEntry,
    InboxProtocol,
    FollowGraph,
    FanoutStrategy,
    Wakeup,
    SignalBusProtocol,
    RunRetryPolicy,
    Lease,
    SchedulerProtocol,
    RunHandle,
    RunResult,
    SupervisorProtocol,
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
    "ReasoningBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "UnknownBlock",
    "ChatMessage",
    "ContentBlock",
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
    "ToolUI",
    "ToolCallRequest",
    "ToolExecutionResult",
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
    "Payload",
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
    "MiddlewareStage",
    # Errors
    "KernelError",
    "AgentCrashError",
    "BudgetExhaustedError",
    "MiddlewareTermination",
    "CancellationError",
    "ConcurrentAppendError",
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
    # HITL
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalResult",
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
    "EventLogProtocol",
    "Effect",
    "EffectResult",
    "DeadLetterReason",
    "DeadLetterEntry",
    "InboxProtocol",
    "FollowGraph",
    "FanoutStrategy",
    "Wakeup",
    "SignalBusProtocol",
    "RunRetryPolicy",
    "Lease",
    "SchedulerProtocol",
    "RunHandle",
    "RunResult",
    "SupervisorProtocol",
    "AgentRunContext",
    "Agent",
    "AskOutcome",
    "RunStatusSummary",
]

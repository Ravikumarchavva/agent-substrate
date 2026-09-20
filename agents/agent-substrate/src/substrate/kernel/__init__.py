"""substrate.kernel — frozen contracts layer.

Everything here is a Protocol, pure dataclass, or value type.
No I/O, no concrete implementations, no external dependencies beyond pydantic.
"""

from __future__ import annotations

from substrate.kernel.core.content import (
    JsonObject,
    Role,
    KernelModel,
    MediaBlock,
    TextBlock,
    DataBlock,
    ErrorBlock,
    ReasoningBlock,
    ToolUseBlock,
    ToolResultBlock,
    UnknownBlock,
    ChatMessage,
    ContentBlock,
    ContentBlockAdapter,
    parse_content_block,
    content_blocks_to_str,
)
from substrate.kernel.exceptions import (
    KernelError,
    ControlSignal,
    TransientError,
    PermanentError,
    PolicyTermination,
    BlockValidationError,
    UnsupportedContentError,
    AgentCrashError,
    BudgetExhaustedError,
    MiddlewareTermination,
    CancellationError,
    SuspendInterrupt,
    ConcurrentAppendError,
    ThreadBusyError,
    BranchHeadConflictError,
    SnapshotConflictError,
    BranchNotFoundError,
    BranchAlreadyExistsError,
    DAGIntegrityError,
)
from substrate.kernel.core.identity import (
    Actor,
    Topic,
)
from substrate.kernel.agent.supervision import (
    Supervision,
    HistoryRetention,
    Priority,
    SpawnBudget,  
    ExecutionBudget,
)
from substrate.kernel.tools.tools import (
    PayloadBase,
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
from substrate.kernel.storage.history import (
    Branch,
    HistoryCheckpoint,
    HistoryProvider,
    MessageNode,
)
from substrate.kernel.agent.context import (
    CompactionStrategy,
    CompactionPhase,
    CompactionContext,
    CompactionResult,
    ContextBuilder,
    ContextWindow,
)
from substrate.kernel.agent.middleware import MiddlewareStage
from substrate.kernel.agent.safety import (
    Severity,
    max_severity,
    SafetyVerdict,
    TextSafetyClassifier,
    ImageSafetyClassifier,
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
from substrate.kernel.storage.memory import (
    ContextMemoryInjection,
    MemoryCategory,
    MemoryMatch,
    MemoryNamespace,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryStatus,
    MemoryStore,
    ShortTermMemory,
)
from substrate.kernel.storage.tasks import Task, TaskList, TaskStatus, TaskStore
from substrate.kernel.document import (
    DocumentChunk,
    DocumentChunker,
    DocumentExtractor,
    DocumentMetadata,
    DocumentStore,
    ExtractedImage,
    ExtractedPage,
    ExtractionResult,
)
from substrate.kernel.storage.snapshots import (
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
    WorkspaceStore,
)
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
    RunLogKind,
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
    "KernelModel",
    "MediaBlock",
    "TextBlock",
    "DataBlock",
    "ErrorBlock",
    "ReasoningBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "UnknownBlock",
    "ChatMessage",
    "ContentBlock",
    "ContentBlockAdapter",
    "parse_content_block",
    "content_blocks_to_str",
    # Exceptions
    "KernelError",
    "ControlSignal",
    "TransientError",
    "PermanentError",
    "PolicyTermination",
    "BlockValidationError",
    "UnsupportedContentError",
    "AgentCrashError",
    "BudgetExhaustedError",
    "MiddlewareTermination",
    "CancellationError",
    "SuspendInterrupt",
    "ConcurrentAppendError",
    "ThreadBusyError",
    "BranchHeadConflictError",
    "SnapshotConflictError",
    "BranchNotFoundError",
    "BranchAlreadyExistsError",
    "DAGIntegrityError",
    # Identity
    "Actor",
    "Topic",
    # Supervision
    "Supervision",
    "HistoryRetention",
    "Priority",
    "SpawnBudget",
    "ExecutionBudget",
    # Tools
    "ToolRisk",
    "PayloadBase",
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
    "MessageNode",
    "Branch",
    "HistoryCheckpoint",
    # Context
    "CompactionStrategy",
    "CompactionPhase",
    "CompactionContext",
    "CompactionResult",
    "ContextBuilder",
    "ContextWindow",
    # Middleware
    "MiddlewareStage",
    # Safety
    "Severity",
    "max_severity",
    "SafetyVerdict",
    "TextSafetyClassifier",
    "ImageSafetyClassifier",
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
    "MemoryRecord",
    "MemoryMatch",
    "MemoryCategory",
    "MemoryStatus",
    "MemoryNamespace",
    "MemoryProvenance",
    "MemoryQuery",
    "ContextMemoryInjection",
    "ShortTermMemory",
    "MemoryStore",
    # Tasks
    "Task",
    "TaskList",
    "TaskStatus",
    "TaskStore",
    # Document intelligence & storage
    "DocumentExtractor",
    "DocumentChunker",
    "DocumentStore",
    "DocumentMetadata",
    "DocumentChunk",
    "ExtractedImage",
    "ExtractedPage",
    "ExtractionResult",
    # Workspace snapshots
    "WorkspaceFileEntry",
    "WorkspaceManifest",
    "WorkspaceSnapshot",
    "WorkspaceStore",
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
    "RunLogKind",
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

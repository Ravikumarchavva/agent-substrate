"""ravi.kernel — frozen contracts layer.

Everything here is a Protocol, pure dataclass, or value type.
No I/O, no concrete implementations, no external dependencies beyond pydantic.
"""

from __future__ import annotations

from ravi.kernel.content import (
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
from ravi.kernel.identity import (
    AgentId,
    TopicId,
)
from ravi.kernel.supervision import (
    Supervision,
    HistoryRetention,
    Priority,
)
from ravi.kernel.message import (
    ChatPayload,
    ToolCallPayload,
    ToolResultPayload,
    DataPayload,
    ControlPayload,
    ProgressPayload,
    Payload,
    register_payload_type,
    ToolCallRequest,
    ToolExecutionResult,
    RuntimeRef,
    Message,
    MessageContext,
    MessageHandler,
    Subscription,
)
from ravi.kernel.protocol import AgentRuntime
from ravi.kernel.tools import (
    ToolRisk,
    ToolType,
    ToolUI,
    ToolExecutionResult as _ToolExecutionResultFromTools,  # noqa: F401 — re-exported via message
    ToolCallRequest as _ToolCallRequestFromTools,  # noqa: F401
    Tool,
    ToolRegistry,
)
from ravi.kernel.skills import Skill
from ravi.kernel.usage import Usage
from ravi.kernel.llm import (
    GenerationOptions,
    LLMClient,
    LLMResponse,
    EmbeddingClient,
    EmbeddingResult,
)
from ravi.kernel.history import HistoryProvider
from ravi.kernel.context import CompactionStrategy, AgentContextProtocol
from ravi.kernel.middleware import (
    Middleware,
    AgentMiddleware,
    ChatMiddleware,
    FunctionMiddleware,
    AgentRunContextProtocol,
    ChatContextProtocol,
    FunctionContextProtocol,
)
from ravi.kernel.errors import (
    KernelError,
    AgentNotFoundError,
    HandlerError,
    AgentCrashError,
    BudgetExhaustedError,
    MiddlewareTermination,
    CancellationError,
)
from ravi.kernel.stream import (
    TextDelta,
    ReasoningDelta,
    CompletionEvent,
    StreamDone,
    AgentProgress,
    AgentStep,
)
from ravi.kernel.vector import Document, SearchResult, VectorStore
from ravi.kernel.graph import Entity, Relationship, SubGraph, GraphStore, CypherCapable
from ravi.kernel.memory import Memory, ShortTermMemory, LongTermMemory
from ravi.kernel.runtime_context import CancellationToken, RunContext
from ravi.kernel.agent import Agent, Checkpoint
from ravi.kernel.events import Event, EventHandler, EventPublisher, EventSubscriber
from ravi.kernel.approval import ApprovalDecision, ApprovalRequest, ApprovalHandler

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
    # Payload types
    "ChatPayload",
    "ToolCallPayload",
    "ToolResultPayload",
    "DataPayload",
    "ControlPayload",
    "ProgressPayload",
    "Payload",
    "register_payload_type",
    # Compat shims (canonical in tools.py)
    "ToolCallRequest",
    "ToolExecutionResult",
    # Communication
    "RuntimeRef",
    "Message",
    "MessageContext",
    "MessageHandler",
    "Subscription",
    # Runtime
    "AgentRuntime",
    # Tools
    "ToolRisk",
    "ToolType",
    "ToolUI",
    "Tool",
    "ToolRegistry",
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
    # Token stream
    "TextDelta",
    "ReasoningDelta",
    "CompletionEvent",
    "StreamDone",
    # Progress stream
    "AgentProgress",
    "AgentStep",
    # Retrieval / knowledge stores
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
    "RunContext",
    # Agent protocol
    "Agent",
    "Checkpoint",
    # Events
    "Event",
    "EventHandler",
    "EventPublisher",
    "EventSubscriber",
    # HITL
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalHandler",
]

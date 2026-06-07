"""ravi.kernel — frozen contracts layer.

Everything here is a Protocol, pure dataclass, or value type.
No I/O, no concrete implementations, no external dependencies beyond pydantic.
"""

from __future__ import annotations

from ravi.kernel.content import (
    JsonObject,
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
    ChatMessage,
    ContentBlock,
    CONTENT_BLOCK_TYPES,
    content_block_from_dict,
    content_blocks_to_str,
)
from ravi.kernel.identity import (
    AgentId,
    TopicId,
    Supervision,
    HistoryRetention,
    Priority,
)
from ravi.kernel.message import (
    ToolCallRequest,
    ToolExecutionResult,
    RuntimeRef,
    Message,
    MessageContext,
    MessageHandler,
    Subscription,
)
from ravi.kernel.protocol import AgentRuntime
from ravi.kernel.tools import ToolRisk, ToolUI, Tool, Toolbox
from ravi.kernel.skills import Skill
from ravi.kernel.usage import Usage
from ravi.kernel.llm import LLMClient, EmbeddingClient, LLMResponse
from ravi.kernel.history import HistoryProvider
from ravi.kernel.context import CompactionStrategy, AgentContextProtocol
from ravi.kernel.middleware import AgentMiddleware, ChatMiddleware, FunctionMiddleware
from ravi.kernel.errors import (
    AgentNotFoundError,
    HandlerError,
    AgentCrashError,
    BudgetExhaustedError,
    MiddlewareTermination,
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
from ravi.kernel.graph import Entity, Relationship, SubGraph, GraphStore
from ravi.kernel.memory import Memory, ShortTermMemory, LongTermMemory

__all__ = [
    # Content
    "JsonObject",
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
    "ChatMessage",
    "ContentBlock",
    "CONTENT_BLOCK_TYPES",
    "content_block_from_dict",
    "content_blocks_to_str",
    # Routing & supervision
    "AgentId",
    "TopicId",
    "Supervision",
    "HistoryRetention",
    "Priority",
    # Tool message payloads
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
    "ToolUI",
    "Tool",
    "Toolbox",
    # Skills
    "Skill",
    # LLM
    "LLMClient",
    "EmbeddingClient",
    "Usage",
    "LLMResponse",
    # History
    "HistoryProvider",
    # Context
    "CompactionStrategy",
    "AgentContextProtocol",
    # Middleware
    "AgentMiddleware",
    "ChatMiddleware",
    "FunctionMiddleware",
    # Errors
    "AgentNotFoundError",
    "HandlerError",
    "AgentCrashError",
    "BudgetExhaustedError",
    "MiddlewareTermination",
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
    # Memory
    "Memory",
    "ShortTermMemory",
    "LongTermMemory",
]

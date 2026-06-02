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
    ChatMessage,
    ContentBlock,
    CONTENT_BLOCK_TYPES,
    content_block_from_dict,
    content_blocks_to_str,
)
from ravi.kernel.identity import (
    AgentId,
    TopicId,
    Supervision as Supervision,
    HistoryRetention as HistoryRetention,
    Priority as Priority,
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
from ravi.kernel.tools import ToolRisk, Tool, Toolbox
from ravi.kernel.skills import Skill
from ravi.kernel.llm import LLMClient, EmbeddingClient
from ravi.kernel.history import HistoryProvider
from ravi.kernel.context import CompactionStrategy, AgentContextProtocol
from ravi.kernel.middleware import Interceptor
from ravi.kernel.errors import (
    AgentNotFoundError,
    HandlerError,
    AgentCrashError as AgentCrashError,
    BudgetExhaustedError as BudgetExhaustedError,
)
from ravi.kernel.stream import (
    TextDelta,
    ReasoningDelta,
    CompletionEvent,
    StreamDone,
    AgentProgress,
    AgentStep,
)

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
    "Tool",
    "Toolbox",
    # Skills
    "Skill",
    # LLM
    "LLMClient",
    "EmbeddingClient",
    # History
    "HistoryProvider",
    # Context
    "CompactionStrategy",
    "AgentContextProtocol",
    # Middleware
    "Interceptor",
    # Errors
    "AgentNotFoundError",
    "HandlerError",
    "AgentCrashError",
    "BudgetExhaustedError",
    # Token stream
    "TextDelta",
    "ReasoningDelta",
    "CompletionEvent",
    "StreamDone",
    # Progress stream
    "AgentProgress",
    "AgentStep",
]

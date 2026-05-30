"""ravi.kernel — the agent runtime core.

Everything an agent framework needs
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
from ravi.kernel.identity import AgentId, TopicId
from ravi.kernel.message import (
    RuntimeRef,
    Message,
    MessageContext,
    MessageHandler,
    Subscription,
)
from ravi.kernel.protocol import AgentRuntime
from ravi.kernel.tools import ToolRisk, ToolCallRequest, ToolExecutionResult, Tool
from ravi.kernel.errors import AgentNotFoundError, HandlerError
from ravi.kernel.stream import (
    TextDelta,
    ReasoningDelta,
    CompletionEvent,
    StreamDone,
    StreamPublisher,
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
    # Routing
    "AgentId",
    "TopicId",
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
    "ToolCallRequest",
    "ToolExecutionResult",
    "Tool",
    # Errors
    "AgentNotFoundError",
    "HandlerError",
    # Stream
    "TextDelta",
    "ReasoningDelta",
    "CompletionEvent",
    "StreamDone",
    "StreamPublisher",
]

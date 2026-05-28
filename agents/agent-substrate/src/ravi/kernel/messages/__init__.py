"""Core message types — public API.

Re-exports all message types and content models from their defining modules.
"""

from ravi.kernel.messages.base_message import (
    BaseClientMessage,
    BaseAgentMessage,
    BaseAgentEvent,
    SOURCE_ROLES,
    UsageStats,
)
from ravi.kernel.messages.client_messages import (
    SystemMessage,
    UserMessage,
    AssistantMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
)
from ravi.kernel.messages.content import (
    # Content blocks (tool results)
    TextBlock,
    ImageBlock,
    AudioBlock,
    VideoBlock,
    DocumentBlock,
    DataBlock,
    CodeBlock,
    ErrorBlock,
    ResourceBlock,
    ContentBlock,
    # Media content (message attachments)
    ImageContent,
    AudioContent,
    VideoContent,
    MediaContent,
    MessageContent,
    # JSON types
    JsonPrimitive,
    JsonValue,
    JsonObject,
    # Helpers
    content_block_from_dict,
    content_blocks_to_str,
)
from ravi.kernel.messages._types import (
    # Backward compat
    MediaType,
    # Stream chunks
    StreamChunk,
    TextDeltaChunk,
    ReasoningDeltaChunk,
    CompletionChunk,
    StructuredOutputChunk,
)

__all__ = [
    # Base messages
    "BaseClientMessage",
    "BaseAgentMessage",
    "BaseAgentEvent",
    "SOURCE_ROLES",
    "UsageStats",
    # Client messages
    "SystemMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolCallMessage",
    "ToolExecutionResultMessage",
    # Content types
    "TextBlock",
    "ImageBlock",
    "AudioBlock",
    "VideoBlock",
    "DocumentBlock",
    "DataBlock",
    "CodeBlock",
    "ErrorBlock",
    "ResourceBlock",
    "ContentBlock",
    "ImageContent",
    "AudioContent",
    "VideoContent",
    "MediaContent",
    "MessageContent",
    "JsonPrimitive",
    "JsonValue",
    "JsonObject",
    "content_block_from_dict",
    "content_blocks_to_str",
    # Backward compat
    "MediaType",
    # Stream chunks
    "StreamChunk",
    "TextDeltaChunk",
    "ReasoningDeltaChunk",
    "CompletionChunk",
    "StructuredOutputChunk",
]

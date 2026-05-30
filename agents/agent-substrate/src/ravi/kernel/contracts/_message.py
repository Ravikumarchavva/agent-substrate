"""Provider-neutral canonical message types.

The existing ``kernel/messages/`` module has provider-shaped classes
(SystemMessage, UserMessage, AssistantMessage, …). Provider encoders in
``integrations/llm/`` translate those to wire formats.

``CanonicalMessage`` is the UNIFIED representation that every encoder
translates TO and FROM. Eliminates the ``call_id`` / ``tool_use_id``
drift between providers (Sprint 2 will wire this through).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from ravi.kernel.messages.content import ContentBlock, JsonObject


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCallSpec(BaseModel):
    """A single tool call requested by the assistant in one LLM turn."""

    call_id: str
    name: str
    arguments: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": True}


class CanonicalMessage(BaseModel):
    """Provider-neutral message.

    Rules:
    - ``role == TOOL`` requires ``tool_call_id`` and ``name``.
    - ``role == ASSISTANT`` with tool requests sets ``tool_calls``.
    - ``content`` holds all multimodal blocks (text, images, audio, …).
    - No provider-specific fields appear here.
    """

    role: MessageRole
    content: list[ContentBlock] = Field(default_factory=list)

    # Assistant turn with tool requests
    tool_calls: Optional[list[ToolCallSpec]] = None

    # Tool result message
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True, "frozen": False}

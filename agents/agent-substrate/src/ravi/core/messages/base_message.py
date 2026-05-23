"""Base message types for the agent framework.

All message types are Pydantic models with a ``type`` discriminator field.
Serialization is handled by Pydantic's built-in ``model_dump(mode="json")``
and deserialization by ``model_validate()``.  The old ``to_dict()`` /
``from_dict()`` manual serde is removed.

Hierarchy::

    BaseClientMessage[ContentT]     — LLM API messages
        SystemMessage               → content: str
        UserMessage                 → content: list[MessageContent]
        AssistantMessage            → content: Optional[list[MessageContent]]
        ToolCallMessage             → content: Optional[str]
        ToolExecutionResultMessage  → content: list[ContentBlock]

    BaseAgentMessage                — agent-to-agent messages
    BaseAgentEvent                  — agent events (tool execution, etc.)
"""

from __future__ import annotations

from abc import ABC
from datetime import datetime, timezone
from typing import Generic, List, Literal, Optional, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from ravi.core.messages.content import JsonObject

CLIENT_ROLES = Literal["system", "user", "assistant", "tool_call", "tool_response"]
SOURCE_ROLES = Literal["user", "agent"]
ContentT = TypeVar("ContentT")


class UsageStats(BaseModel):
    """Token usage statistics for a single LLM call.

    The ``extra`` dict holds provider-specific metrics (e.g. Anthropic
    ``cache_read_input_tokens``, ``cache_creation_input_tokens``).
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    extra: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": False}

    @model_validator(mode="after")
    def _normalize_total(self) -> "UsageStats":
        """Compute total when provider omits it (e.g. some cached responses)."""
        if self.total_tokens == 0 and (self.prompt_tokens > 0 or self.completion_tokens > 0):
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        return self


class BaseClientMessage(BaseModel, ABC, Generic[ContentT]):
    """Base message class for client-model communication (LLM API).

    Subclasses narrow the ``content`` field to a concrete type.

    Serialization: use ``model_dump(mode="json")`` to serialize and
    ``model_validate(data)`` to deserialize.  Provider-specific
    encoding lives in ``core.messages.encoders.<provider>``.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    role: CLIENT_ROLES
    content: ContentT
    type: Literal["BaseClientMessage"] = "BaseClientMessage"

    model_config = {"arbitrary_types_allowed": True}


class BaseAgentMessage(BaseModel, ABC):
    """Base message class for agent-to-agent communication."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    source: SOURCE_ROLES
    model_usage: Optional[UsageStats] = None
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_client_messages(self) -> List[BaseClientMessage]:
        """Convert agent message to client message(s) for model consumption.

        Subclasses must override this to produce the correct message format.
        """
        raise NotImplementedError


class BaseAgentEvent(BaseModel, ABC):
    """Base class for agent events (tool execution, thinking, etc.)."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    source: str
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

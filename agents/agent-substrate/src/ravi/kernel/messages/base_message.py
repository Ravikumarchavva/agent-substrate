"""Base message types for LLM API communication."""

from __future__ import annotations

from abc import ABC
from typing import Generic, Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from ravi.kernel.messages.content import JsonObject

CLIENT_ROLES = Literal["system", "user", "assistant", "tool_call", "tool_response"]
ContentT = TypeVar("ContentT")


class UsageStats(BaseModel):
    """Token usage statistics for a single LLM call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    extra: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": False}

    @model_validator(mode="after")
    def _normalize_total(self) -> "UsageStats":
        if self.total_tokens == 0 and (
            self.prompt_tokens > 0 or self.completion_tokens > 0
        ):
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        return self


class BaseClientMessage(BaseModel, ABC, Generic[ContentT]):
    """Base for LLM API messages — provider-agnostic data containers.

    Provider-specific encoding lives in ``integrations/llm/encoders/``.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    role: CLIENT_ROLES
    content: ContentT
    type: Literal["BaseClientMessage"] = "BaseClientMessage"

    model_config = {"arbitrary_types_allowed": True}

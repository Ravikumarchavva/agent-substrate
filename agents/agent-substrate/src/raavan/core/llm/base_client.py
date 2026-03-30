from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional, Type

from raavan.core.messages.client_messages import (
    BaseClientMessage,
    AssistantMessage,
)

if TYPE_CHECKING:
    from pydantic import BaseModel


class BaseModelClient(ABC):
    """Base class for all model clients (OpenAI, Anthropic, etc.)."""

    def __init__(
        self,
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.kwargs = kwargs

    @abstractmethod
    async def generate(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict]] = None,
        *,
        tool_choice: Optional[str | dict[str, Any]] = None,
        response_format: Optional["Type[BaseModel]"] = None,
        **kwargs: Any,
    ) -> Any:
        """Generate a response from the model.

        - When ``response_format`` is ``None``: returns ``AssistantMessage``.
        - When ``response_format`` is a Pydantic schema: returns
          ``StructuredOutputResult``.
        """
        raise NotImplementedError

    @abstractmethod
    async def generate_stream(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict]] = None,
        **kwargs,
    ) -> AsyncIterator[AssistantMessage]:
        """Generate a streaming response from the model."""
        if False:
            # Marks this abstract method as an async-generator contract.
            yield AssistantMessage(role="assistant", content=None)

    @abstractmethod
    async def count_tokens(self, messages: list[BaseClientMessage]) -> int:
        """Count tokens in messages."""
        pass

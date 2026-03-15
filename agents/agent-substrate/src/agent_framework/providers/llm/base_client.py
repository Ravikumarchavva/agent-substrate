from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional, Type

from agent_framework.core.messages.client_messages import BaseClientMessage, AssistantMessage

if TYPE_CHECKING:
    from pydantic import BaseModel
    from agent_framework.core.structured.result import StructuredOutputResult

class BaseModelClient(ABC):
    """Base class for all model clients (OpenAI, Anthropic, etc.)."""
    
    def __init__(
        self,
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
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
        **kwargs
    ) -> AssistantMessage:
        """Generate a single response from the model."""
        pass
    
    @abstractmethod
    async def generate_stream(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict]] = None,
        **kwargs
    ) -> AsyncIterator[AssistantMessage]:
        """Generate a streaming response from the model."""
        pass

    @abstractmethod
    async def generate_structured(
        self,
        messages: list[BaseClientMessage],
        response_schema: "Type[BaseModel]",
        **kwargs,
    ) -> "StructuredOutputResult":
        """Generate a response that conforms to ``response_schema``.

        Uses the provider's native structured-output mechanism (e.g. OpenAI
        Responses API ``text_format`` / ``responses.parse``) to guarantee
        schema adherence.

        Args:
            messages: Conversation messages (SystemMessage, UserMessage, etc.).
            response_schema: A Pydantic ``BaseModel`` subclass describing the
                desired output shape.  The implementation converts it to the
                required wire format automatically.
            **kwargs: Provider-specific overrides (model, temperature, etc.).

        Returns:
            ``StructuredOutputResult`` with ``parsed``, ``raw_text``, and
            ``refusal`` fields.

        Raises:
            StructuredOutputError: On unrecoverable parse / API failure.
        """
        pass

    @abstractmethod
    def count_tokens(self, messages: list[BaseClientMessage]) -> int:
        """Count tokens in messages."""
        pass
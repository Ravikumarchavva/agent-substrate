from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional
from agent_framework.messages.client_messages import BaseClientMessage, AssistantMessage

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
    def count_tokens(self, messages: list[BaseClientMessage]) -> int:
        """Count tokens in messages."""
        pass
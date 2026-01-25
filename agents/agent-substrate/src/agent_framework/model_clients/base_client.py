from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional
from agent_framework.messages.base_message import BaseMessage


class ModelResponse(BaseMessage):
    """Structured response from model client."""
    usage: Optional[dict[str, Any]] = None
    model: Optional[str] = None
    finish_reason: Optional[str] = None

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update({
            "usage": self.usage,
            "model": self.model,
            "finish_reason": self.finish_reason,
        })
        return data
    
    def from_dict(cls, data: dict) -> "ModelResponse":
        return cls(
            id=data.get("id", ""),
            role=data.get("role", ""),
            content=data.get("content", ""),
            timestamp=data.get("timestamp"),
            metadata=data.get("metadata", {}),
            usage=data.get("usage"),
            model=data.get("model"),
            finish_reason=data.get("finish_reason"),
        )


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
        messages: list[BaseMessage],
        tools: Optional[list[dict]] = None,
        **kwargs
    ) -> ModelResponse:
        """Generate a single response from the model."""
        pass
    
    @abstractmethod
    async def generate_stream(
        self,
        messages: list[BaseMessage],
        tools: Optional[list[dict]] = None,
        **kwargs
    ) -> AsyncIterator[ModelResponse]:
        """Generate a streaming response from the model."""
        pass
    
    @abstractmethod
    def count_tokens(self, messages: list[BaseMessage]) -> int:
        """Count tokens in messages."""
        pass
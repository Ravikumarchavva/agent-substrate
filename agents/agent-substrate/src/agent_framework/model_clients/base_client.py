from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional
from agent_framework.messages.base_message import BaseMessage
from agent_framework.messages.agent_messages import ToolCall


class ModelResponse(BaseMessage):
    """Structured response from model client."""
    tool_calls: Optional[list[ToolCall]] = None
    usage: Optional[dict[str, Any]] = None
    model: Optional[str] = None
    finish_reason: Optional[str] = None

    def to_dict(self) -> dict:
        data = {
            "role": self.role,
            "content": self.content,
        }
        
        if self.tool_calls:
            data["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": tc.function
                }
                for tc in self.tool_calls
            ]
            
        if self.usage:
            data["usage"] = self.usage
        if self.model:
            data["model"] = self.model
        if self.finish_reason:
            data["finish_reason"] = self.finish_reason
            
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> "ModelResponse":
        tool_calls = None
        if "tool_calls" in data and data["tool_calls"]:
            tool_calls = [ToolCall(**tc) for tc in data["tool_calls"]]

        kwargs = {
            "role": data.get("role", "assistant"), # Default to assistant?
            "content": data.get("content", ""),
            "metadata": data.get("metadata", {}),
            "tool_calls": tool_calls,
            "usage": data.get("usage"),
            "model": data.get("model"),
            "finish_reason": data.get("finish_reason"),
        }
        
        if "id" in data:
            kwargs["id"] = data["id"]
        
        if "timestamp" in data:
            kwargs["timestamp"] = data["timestamp"]

        return cls(**kwargs)


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
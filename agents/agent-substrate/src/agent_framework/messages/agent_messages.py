from .base_message import BaseMessage
from typing import Any, Optional, Literal
from pydantic import Field, BaseModel

class SystemMessage(BaseMessage):
    """System message for agent instructions."""
    role: Literal["system"] = "system"
    content: str
    
    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "SystemMessage":
        return cls(content=data["content"])


class UserMessage(BaseMessage):
    """User message with text or multimodal content."""
    role: Literal["user"] = "user"
    content: str | list[dict[str, Any]]  # Support text or multimodal
    name: Optional[str] = None  # Optional user identifier
    
    def to_dict(self) -> dict:
        msg = {
            "role": self.role,
            "content": self.content
        }
        if self.name:
            msg["name"] = self.name
        return msg
    
    @classmethod
    def from_dict(cls, data: dict) -> "UserMessage":
        return cls(
            content=data["content"],
            name=data.get("name")
        )


class ToolCall(BaseModel):
    """Represents a single tool call."""
    id: str
    type: Literal["function"] = "function"
    function: dict[str, Any]  # {"name": str, "arguments": str (JSON)}
    
    class Config:
        arbitrary_types_allowed = True


class AssistantMessage(BaseMessage):
    """Assistant message with optional tool calls."""
    role: Literal["assistant"] = "assistant"
    content: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None
    name: Optional[str] = None
    
    def to_dict(self) -> dict:
        msg = {"role": self.role}
        if self.content:
            msg["content"] = self.content
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": tc.function
                }
                for tc in self.tool_calls
            ]
        if self.name:
            msg["name"] = self.name
        return msg
    
    @classmethod
    def from_dict(cls, data: dict) -> "AssistantMessage":
        tool_calls = None
        if "tool_calls" in data and data["tool_calls"]:
            tool_calls = [ToolCall(**tc) for tc in data["tool_calls"]]
        
        return cls(
            content=data.get("content"),
            tool_calls=tool_calls,
            name=data.get("name")
        )


class ToolMessage(BaseMessage):
    """Tool execution result message."""
    role: Literal["tool"] = "tool"
    content: str  # Tool result (usually JSON string)
    tool_call_id: str  # Links back to the tool call
    name: Optional[str] = None  # Tool name
    
    def to_dict(self) -> dict:
        msg = {
            "role": self.role,
            "content": self.content,
            "tool_call_id": self.tool_call_id
        }
        if self.name:
            msg["name"] = self.name
        return msg
    
    @classmethod
    def from_dict(cls, data: dict) -> "ToolMessage":
        return cls(
            content=data["content"],
            tool_call_id=data["tool_call_id"],
            name=data.get("name")
        )
    

    

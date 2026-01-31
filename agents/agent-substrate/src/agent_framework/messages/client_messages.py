from typing import Any, Dict, List, Optional, Union, Literal
from pydantic import ConfigDict, field_validator, model_serializer
from .base_message import BaseClientMessage, CLIENT_ROLES, UsageStats

from agent_framework.messages._types import (
    MediaType, ToolResponseContent,
    serialize_media_content, deserialize_media_content, 
    serialize_tool_response_content, deserialize_tool_response_content
)


class SystemMessage(BaseClientMessage):
    """System message for agent instructions."""
    role: CLIENT_ROLES = "system"
    content: str
    type: Literal["SystemMessage"] = "SystemMessage"
    
    def to_dict(self) -> Dict:
        return {
            "role": self.role,
            "content": self.content,
            "type": self.type
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "SystemMessage":
        return cls(content=data["content"])

class UserMessage(BaseClientMessage):
    """User message with text or multimodal content."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: CLIENT_ROLES = "user"
    content: List[MediaType]
    name: Optional[str] = None
    type: Literal["UserMessage"] = "UserMessage"
    
    @model_serializer
    def ser_model(self) -> Dict[str, Any]:
        serialized_content = [
            serialize_media_content(item) for item in self.content
        ]
        msg = {
            "role": self.role,
            "content": serialized_content,
            "type": self.type,
        }
        if self.name:
            msg["name"] = self.name
        return msg
    @field_validator("content", mode="before")

    def des_content(cls, v: Any) -> List[MediaType]:
        if isinstance(v, list):
            return [deserialize_media_content(item) for item in v]
        else:
            raise ValueError("Content must be a list")
    
class ToolCallMessage(BaseClientMessage):
    """Represents a single tool call."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: CLIENT_ROLES = "tool_call"
    name: str
    content: Dict[str, Any]
    type: Literal["ToolCallMessage"] = "ToolCallMessage"

    @model_serializer
    def ser_model(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "name": self.name,
            "content": self.content,
            "type": self.type,
        }
    
    @field_validator("content", mode="before")
    @classmethod
    def des_content(cls, v: Any) -> Dict[str, Any]:
        if isinstance(v, dict):
            return v
        else:
            raise ValueError("Content must be a dictionary")

class AssistantMessage(BaseClientMessage):
    """Assistant message with optional tool calls."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    type: Literal["AssistantMessage"] = "AssistantMessage"
    role: CLIENT_ROLES = "assistant"
    name: Optional[str] = None
    reasoning: Optional[str] = None
    content: Optional[List[MediaType]] = None
    tool_calls: Optional[List[ToolCallMessage]] = None
    finish_reason: str = "stop"  # e.g., "stop", "tool_call", etc.
    usage: UsageStats = None
    cached: bool = False # Indicates if response used input caching or not

    @model_serializer
    def ser_model(self) -> Dict[str, Any]:
        msg: Dict[str, Any] = {
            "role": self.role,
            "finish_reason": self.finish_reason,
            "cached": self.cached,
            "type": self.type,
        }
        if self.name:
            msg["name"] = self.name
        if self.reasoning:
            msg["reasoning"] = self.reasoning
        if self.content is not None:
            serialized_content = [
                serialize_media_content(item) for item in self.content
            ]
            msg["content"] = serialized_content
        if self.tool_calls is not None:
            msg["tool_calls"] = [tc.ser_model() for tc in self.tool_calls]
        if self.usage is not None:
            msg["usage"] = {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens,
            }
        return msg

class ToolExecutionResultMessage(BaseClientMessage):
    """Tool execution result message."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: CLIENT_ROLES = "tool_response"
    tool_call_id: str  # Links back to the tool call
    name: Optional[str] = None  # Tool name
    content: List[ToolResponseContent]
    is_error: bool = False
    type: Literal["ToolExecutionResultMessage"] = "ToolExecutionResultMessage"

    @model_serializer
    def ser_model(self) -> Dict[str, Any]:
        serialized_content = [
            serialize_tool_response_content(item) for item in self.content
        ]
        msg = {
            "role": self.role,
            "tool_call_id": self.tool_call_id,
            "content": serialized_content,
            "is_error": self.is_error,
            "type": self.type,
        }
        if self.name:
            msg["name"] = self.name
        return msg
    
    @field_validator("content", mode="before")
    def des_content(cls, v: Any) -> List[ToolResponseContent]:
        if isinstance(v, list):
            return [deserialize_tool_response_content(item) for item in v]
        else:
            raise ValueError("Content must be a list")

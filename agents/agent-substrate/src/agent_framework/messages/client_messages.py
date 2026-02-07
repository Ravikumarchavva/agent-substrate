from __future__ import annotations

from typing import Any, Dict, List, Optional, Union, Literal
from pydantic import ConfigDict, field_validator, model_serializer, Field
from .base_message import BaseClientMessage, CLIENT_ROLES, UsageStats
from agent_framework.tools.base_tool import ToolCall as ToolCallDataclass, ToolResult
import json
from dataclasses import asdict
from uuid import uuid4

from agent_framework.messages._types import (
    MediaType,
    serialize_media_content, deserialize_media_content
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
    """Represents a single tool call (MCP-compatible)."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: CLIENT_ROLES = "tool_call"
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    type: Literal["ToolCallMessage"] = "ToolCallMessage"

    @field_validator("arguments", mode="before")
    def validate_arguments(cls, v: Any) -> Dict[str, Any]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                raise ValueError("arguments must be a dict or JSON string")
        if isinstance(v, dict):
            return v
        raise ValueError("arguments must be a dict")

    @model_serializer
    def ser_model(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }
    
    def to_mcp_format(self) -> Dict[str, Any]:
        """Convert to MCP tool call format."""
        return {
            "name": self.name,
            "arguments": self.arguments,
        }
    
    def to_openai_format(self) -> Dict[str, Any]:
        """Convert to OpenAI tool call format."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments),
            },
        }


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
            serialized_tool_calls: List[Dict[str, Any]] = []
            for tc in self.tool_calls:
                if isinstance(tc, ToolCallMessage):
                    serialized_tool_calls.append(tc.ser_model())
                elif isinstance(tc, ToolCallDataclass):
                    # ToolCallDataclass may be a Pydantic BaseModel or a dataclass
                    if hasattr(tc, "model_dump"):
                        serialized_tool_calls.append(tc.model_dump())
                    else:
                        serialized_tool_calls.append(asdict(tc))
                elif isinstance(tc, dict):
                    serialized_tool_calls.append(tc)
                elif hasattr(tc, "model_dump"):
                    serialized_tool_calls.append(tc.model_dump())
                else:
                    serialized_tool_calls.append({"name": getattr(tc, "name", None), "arguments": getattr(tc, "arguments", None)})
            msg["tool_calls"] = serialized_tool_calls
        if self.usage is not None:
            msg["usage"] = {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens,
            }
        return msg

class ToolExecutionResultMessage(BaseClientMessage):
    """Tool execution result message (MCP-compatible)."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: CLIENT_ROLES = "tool_response"
    tool_call_id: str  # Links back to the tool call
    name: Optional[str] = None  # Tool name
    content: List[Dict[str, Any]]  # MCP format: list of content blocks
    isError: bool = False  # MCP naming convention
    type: Literal["ToolExecutionResultMessage"] = "ToolExecutionResultMessage"

    @field_validator("content", mode="before")
    def validate_content(cls, v: Any) -> List[Dict[str, Any]]:
        """Validate and convert content to MCP format."""
        if isinstance(v, str):
            # Convert plain string to MCP text content block
            return [{"type": "text", "text": v}]
        elif isinstance(v, list):
            result = []
            for item in v:
                if isinstance(item, dict):
                    # Already in proper format
                    result.append(item)
                elif isinstance(item, str):
                    # Convert string to text content block
                    result.append({"type": "text", "text": item})
                else:
                    # Try to serialize other types
                    result.append({"type": "text", "text": str(item)})
            return result
        elif isinstance(v, dict):
            # Single dict - wrap in list
            return [v]
        else:
            # Convert to text content block
            return [{"type": "text", "text": str(v)}]

    def to_dict(self) -> Dict:
        """Convert to dictionary format."""
        return self.ser_model()
    
    @classmethod
    def from_dict(cls, data: Dict) -> "ToolExecutionResultMessage":
        """Create from dictionary."""
        return cls(**data)

    @model_serializer
    def ser_model(self) -> Dict[str, Any]:
        msg = {
            "role": self.role,
            "tool_call_id": self.tool_call_id,
            "content": self.content,
            "isError": self.isError,
            "type": self.type,
        }
        if self.name:
            msg["name"] = self.name
        return msg
    
    @classmethod
    def from_tool_result(
        cls, 
        tool_result: ToolResult, 
        tool_call_id: str, 
        tool_name: Optional[str] = None
    ) -> "ToolExecutionResultMessage":
        """Create ToolExecutionResultMessage from ToolResult."""
        return cls(
            tool_call_id=tool_call_id,
            name=tool_name,
            content=tool_result.content,
            isError=tool_result.isError,
        )
    
    def to_mcp_format(self) -> Dict[str, Any]:
        """Convert to MCP tool result format."""
        return {
            "content": self.content,
            "isError": self.isError,
        }
    
    def to_openai_format(self) -> Dict[str, Any]:
        """Convert to OpenAI tool message format."""
        # OpenAI expects simple string content
        text_parts = []
        for block in self.content:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "image":
                text_parts.append("[Image]")
            elif block.get("type") == "resource":
                text_parts.append(f"[Resource: {block.get('resource', {}).get('uri', '')}]")
            else:
                text_parts.append(str(block))
        
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": "\n".join(text_parts) if text_parts else "",
        }

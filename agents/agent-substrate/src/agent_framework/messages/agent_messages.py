"""Agent-to-agent communication messages for multi-agent orchestration."""
from .base_message import BaseAgentMessage, SOURCE_ROLES
from ._types import MediaType
from .client_messages import UserMessage, AssistantMessage, ToolExecutionResultMessage
from typing import List, Literal, Union
from pydantic import Field, BaseModel, ConfigDict


class UserAgentMessage(BaseAgentMessage):
    """Message sent from a user to an agent."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: SOURCE_ROLES = "user"
    content: List[MediaType]
    type: Literal["UserAgentMessage"] = "UserAgentMessage"

    def to_model_client_message(self) -> BaseModel:
        """Convert to UserMessage for model client."""
        return UserMessage(
            role="user",
            content=self.content,
        )
    
    def to_dict(self):
        return {
            "id": self.id,
            "source": self.source,
            "content": self.content,
            "type": self.type,
            "created_at": self.created_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "UserAgentMessage":
        return cls(content=data["content"])


class AgentResponseMessage(BaseAgentMessage):
    """Message sent from an agent back to user or another agent."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: SOURCE_ROLES = "agent"
    content: List[Union[AssistantMessage, ToolExecutionResultMessage]]
    type: Literal["AgentResponseMessage"] = "AgentResponseMessage"

    def to_model_client_message(self) -> List[BaseModel]:
        """Convert to list of client messages for model consumption."""
        messages: List[BaseModel] = []
        for item in self.content:
            if isinstance(item, (AssistantMessage, ToolExecutionResultMessage)):
                messages.append(item)
        return messages
    
    def to_dict(self):
        return {
            "id": self.id,
            "source": self.source,
            "content": [msg.to_dict() for msg in self.content],
            "type": self.type,
            "created_at": self.created_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "AgentResponseMessage":
        # Reconstruct content from dicts
        content = []
        for item in data["content"]:
            msg_type = item.get("type")
            if msg_type == "AssistantMessage":
                content.append(AssistantMessage.from_dict(item))
            elif msg_type == "ToolExecutionResultMessage":
                content.append(ToolExecutionResultMessage.from_dict(item))
        return cls(content=content)


from .base_message import BaseAgentMessage, SOURCE_ROLES
from ._types import (
    MediaType
)
from .client_messages import UserMessage, AssistantMessage, ToolExecutionResultMessage
from typing import List, Literal, Union
from pydantic import Field, BaseModel, ConfigDict

class UserAgentMessage(BaseAgentMessage):
    """Message sent from one user agent to another agent."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: SOURCE_ROLES = "user"
    content: List[MediaType]
    type: Literal["UserAgentMessage"] = "UserAgentMessage"

    def to_model_client_message(self) -> BaseModel:
        return UserMessage(
            role=self.source,
            content=self.content,
        )

class AgentResponseMessage(BaseAgentMessage):
    """Message sent from an agent back to the user."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: SOURCE_ROLES = "agent"
    content: List[Union[AssistantMessage, ToolExecutionResultMessage]]
    type: Literal["AgentResponseMessage"] = "AgentResponseMessage"

    def to_model_client_message(self) -> List[BaseModel]:
        messages: List[BaseModel] = []
        for item in self.content:
            if isinstance(item, AssistantMessage):
                messages.append(item)
            elif isinstance(item, ToolExecutionResultMessage):
                messages.append(item)
        return messages


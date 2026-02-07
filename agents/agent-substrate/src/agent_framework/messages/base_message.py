from abc import ABC, abstractmethod
from pydantic import BaseModel, Field
from typing import Literal, Any, Optional, Dict
from datetime import datetime
from uuid import uuid4

CLIENT_ROLES = Literal["system", "user", "assistant", "tool_call", "tool_response"]
SOURCE_ROLES = Literal["user", "agent"]


class UsageStats(BaseModel):
    """Token usage statistics for a single LLM call.
    
    Pydantic model (not dataclass) so it serializes cleanly everywhere.
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    model_config = {"frozen": False}


class BaseClientMessage(BaseModel, ABC):
    """Base message class for client-model communication (LLM API)."""
    
    id: str = Field(default_factory=lambda: str(uuid4()))
    role: CLIENT_ROLES
    content: Any
    type: Literal["BaseClientMessage"] = "BaseClientMessage"
    
    model_config = {"arbitrary_types_allowed": True}
    
    @abstractmethod
    def to_dict(self) -> Dict:
        """Convert message to dictionary for LLM API."""
        pass

    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict) -> "BaseClientMessage":
        """Create message from dictionary."""
        pass


class BaseAgentMessage(BaseModel, ABC):
    """Base message class for agent-to-agent communication."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    source: SOURCE_ROLES
    model_usage: Optional[UsageStats] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @abstractmethod
    def to_model_client_message(self):
        """Convert agent message to client message(s) for model consumption."""
        pass

    @abstractmethod
    def to_dict(self) -> Dict:
        """Convert agent message to dictionary."""
        pass

    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict) -> "BaseAgentMessage":
        """Create agent message from dictionary."""
        pass


class BaseAgentEvent(BaseModel, ABC):
    """Base class for agent events (tool execution, thinking, etc.)."""
    
    id: str = Field(default_factory=lambda: str(uuid4()))
    source: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

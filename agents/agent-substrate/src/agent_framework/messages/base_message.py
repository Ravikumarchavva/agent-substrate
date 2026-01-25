from abc import ABC, abstractmethod
from pydantic import BaseModel, Field
from typing import Literal, Any, Optional
from datetime import datetime
from uuid import uuid4

ROLES = Literal["system", "user", "assistant", "tool"]

class BaseMessage(BaseModel, ABC):
    """Base message class with common fields for all message types."""
    
    id: str = Field(default_factory=lambda: str(uuid4()))
    role: ROLES
    content: Any
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        arbitrary_types_allowed = True
    
    @abstractmethod
    def to_dict(self) -> dict:
        """Convert message to dictionary for LLM API."""
        pass

    @abstractmethod
    @classmethod
    def from_dict(cls, data: dict) -> "BaseMessage":
        """Create message from dictionary."""
        pass
    
    def to_storage_dict(self) -> dict:
        """Convert message to dictionary for storage (includes all fields)."""
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }
from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Literal, Any

ROLES = Literal["system", "user", "assistant", "tool"]

class BaseMessage(ABC, BaseModel):

    role: ROLES
    content: Any
    
    @abstractmethod
    def to_dict(self) -> dict:
        pass

    @abstractmethod
    @classmethod
    def from_dict(cls, data: dict) -> "BaseMessage":
        pass
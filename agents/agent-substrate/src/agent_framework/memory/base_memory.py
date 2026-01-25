from abc import ABC, abstractmethod
from typing import Optional
from agent_framework.messages.base_message import BaseMessage


class BaseMemory(ABC):
    """Base class for conversation memory management."""
    
    @abstractmethod
    def add_message(self, message: BaseMessage) -> None:
        """Add a message to memory.
        
        Args:
            message: Message to store
        """
        pass

    @abstractmethod
    def get_messages(self, limit: Optional[int] = None) -> list[BaseMessage]:
        """Retrieve messages from memory.
        
        Args:
            limit: Optional limit on number of messages to return
            
        Returns:
            List of messages
        """
        pass
    
    @abstractmethod
    def clear(self) -> None:
        """Clear all messages from memory."""
        pass
    
    @abstractmethod
    def get_token_count(self) -> int:
        """Get approximate token count of stored messages."""
        pass
    
    def __len__(self) -> int:
        """Return number of messages in memory."""
        return len(self.get_messages())
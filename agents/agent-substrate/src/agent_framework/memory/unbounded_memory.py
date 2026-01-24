from .base_memory import BaseMemory
from agent_framework.messages.base_message import BaseMessage
from typing import List

class UnboundedMemory(BaseMemory):
    def __init__(self):
        self.memory: List[BaseMessage] = []

    def store(self, entry: BaseMessage) -> None:
        self.memory.append(entry)

    def retrieve(self) -> List[BaseMessage]:
        return self.memory

    def clear_memory(self) -> None:
        self.memory = []
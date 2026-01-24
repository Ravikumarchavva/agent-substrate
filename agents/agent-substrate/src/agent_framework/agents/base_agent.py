from typing import List
from abc import ABC, abstractmethod

from agent_framework.tools import BaseTool
from agent_framework.model_clients.base_client import BaseModelClient
from agent_framework.memory.base_memory import BaseMemory

class BaseAgent(ABC):
    def __init__(
        self,
        name: str,
        description: str,
        *,
        model_client: BaseModelClient,
        tools: List[BaseTool] | None = None,
        system_instructions: str = "you are a helpful assistant",
        memory: BaseMemory | None = None,
    ):
        self.name = name
        self.description = description
        self.model_client = model_client
        self.tools = tools
        self.memory = memory
        self.system_instructions = system_instructions

    @abstractmethod
    async def run(self, *args, **kwargs):
        pass

    @abstractmethod
    async def run_stream(self, *args, **kwargs):
        pass
    
    @abstractmethod
    def save_state(self):
        pass

    @abstractmethod
    def load_state(self):
        pass
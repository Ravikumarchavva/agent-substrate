"""Base agent contract.

Every agent type (ReAct, Plan-and-Execute, Custom) implements this interface.
The contract is deliberately minimal:
  - run()        → full result
  - run_stream() → async iterator of partial events
  - save/load    → serializable state for checkpointing
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional
from abc import ABC, abstractmethod

from agent_framework.agents.agent_result import AgentRunResult
from agent_framework.tools.base_tool import BaseTool
from agent_framework.model_clients.base_client import BaseModelClient
from agent_framework.memory.base_memory import BaseMemory


class BaseAgent(ABC):
    """Abstract base for all agent implementations."""

    def __init__(
        self,
        name: str,
        description: str,
        *,
        model_client: BaseModelClient,
        tools: Optional[List[BaseTool]] = None,
        system_instructions: str = "You are a helpful assistant.",
        memory: Optional[BaseMemory] = None,
    ):
        self.name = name
        self.description = description
        self.model_client = model_client
        self.tools = tools or []
        self.system_instructions = system_instructions
        self.memory = memory

    # -- Core lifecycle -------------------------------------------------------

    @abstractmethod
    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        """Execute the agent to completion and return a structured result."""
        ...

    @abstractmethod
    async def run_stream(self, input_text: str, **kwargs) -> AsyncIterator[Any]:
        """Execute the agent, yielding events/chunks as they happen."""
        ...

    # -- State management (checkpoint / resume) -------------------------------

    @abstractmethod
    def save_state(self) -> Dict[str, Any]:
        """Serialize agent state for persistence / checkpointing."""
        ...

    @abstractmethod
    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore agent state from a previously saved checkpoint."""
        ...

    # -- Helpers --------------------------------------------------------------

    def reset(self) -> None:
        """Clear memory and return agent to initial state."""
        if self.memory:
            self.memory.clear()

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name!r}, tools={len(self.tools)})>"

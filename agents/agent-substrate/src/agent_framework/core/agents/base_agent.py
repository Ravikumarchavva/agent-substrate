"""Base agent contract.

Every agent type (ReAct, Plan-and-Execute, Custom) implements this interface.
The contract is deliberately minimal:
  - run()        → full result
  - run_stream() → async iterator of partial events
  - save/load    → serializable state for checkpointing
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Union
from abc import ABC, abstractmethod

from agent_framework.core.agents.agent_result import AgentRunResult
from agent_framework.core.context.base_context import ModelContext
from agent_framework.core.tools.base_tool import BaseTool
from agent_framework.providers.llm.base_client import BaseModelClient
from agent_framework.core.memory.base_memory import BaseMemory
from agent_framework.core.memory.memory_scope import MemoryScope
from agent_framework.core.guardrails.base_guardrail import BaseGuardrail
from agent_framework.extensions.skills import SkillManager


class BaseAgent(ABC):
    """Abstract base for all agent implementations."""

    def __init__(
        self,
        name: str,
        description: str,
        *,
        model_client: BaseModelClient,
        model_context: ModelContext,
        tools: Optional[List[BaseTool]] = None,
        system_instructions: str = "You are a helpful assistant.",
        memory: Optional[BaseMemory] = None,
        memory_scope: MemoryScope = MemoryScope.ISOLATED,
        input_guardrails: Optional[List[BaseGuardrail]] = None,
        output_guardrails: Optional[List[BaseGuardrail]] = None,
        # Skills
        skill_dirs: Optional[List[Union[str, Path]]] = None,
        skill_manager: Optional[SkillManager] = None,
    ):
        self.name = name
        self.description = description
        self.model_client = model_client
        self.model_context = model_context
        self.tools = tools or []
        self.system_instructions = system_instructions
        self.memory = memory
        self.memory_scope = memory_scope
        self.input_guardrails = input_guardrails or []
        self.output_guardrails = output_guardrails or []

        # Normalise skill_dirs to List[Path] at construction time so downstream
        # code never has to deal with str vs Path heterogeneity.
        normalised_dirs: Optional[List[Path]] = (
            [Path(d) for d in skill_dirs] if skill_dirs else None
        )

        # Skills: prefer an explicit manager; otherwise build one from dirs
        if skill_manager is not None:
            self.skill_manager: Optional[SkillManager] = skill_manager
        elif normalised_dirs:
            self.skill_manager = SkillManager(skill_dirs=normalised_dirs)
        else:
            self.skill_manager = None

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
    async def save_state(self) -> Dict[str, Any]:
        """Serialize agent state for persistence / checkpointing."""
        ...

    @abstractmethod
    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore agent state from a previously saved checkpoint."""
        ...

    # -- Helpers --------------------------------------------------------------

    async def reset(self) -> None:
        """Clear memory and return agent to initial state."""
        if self.memory:
            await self.memory.clear()

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name!r}, tools={len(self.tools)})>"

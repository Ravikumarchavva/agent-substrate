"""Base agent contract.

Every agent type (ReAct, Plan-and-Execute, Custom) implements this interface.
The contract is deliberately minimal:
  - run()        -> full result
  - run_stream() -> async iterator of partial events
"""

from __future__ import annotations

from typing import Any, AsyncIterator, List, Optional
from abc import ABC, abstractmethod
from typing import runtime_checkable, Protocol

from raavan.core.agents.agent_result import AgentRunResult
from raavan.core.context.base_context import ModelContext
from raavan.core.tools.base_tool import BaseTool
from raavan.core.llm.base_client import BaseModelClient
from raavan.core.memory.base_memory import BaseMemory
from raavan.core.memory.memory_scope import MemoryScope
from raavan.core.guardrails.base_guardrail import BaseGuardrail
from raavan.core.runtime._protocol import AgentId, AgentRuntime
from raavan.core.runtime._types import MessageContext


# ---------------------------------------------------------------------------
# PromptEnricher -- protocol that decouples core from extensions.skills
# ---------------------------------------------------------------------------


@runtime_checkable
class PromptEnricher(Protocol):
    """Anything that can inject extra context into a system prompt.

    ``SkillManager`` (in ``extensions.skills``) implements this protocol via
    duck typing -- no explicit inheritance required.
    """

    def inject_into_prompt(self, system_prompt: str) -> str:
        """Return *system_prompt* augmented with extra context."""
        ...


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
        # Prompt enrichment (replaces skill_manager / skill_dirs coupling)
        prompt_enricher: Optional[PromptEnricher] = None,
        # Runtime — makes the agent distributable
        runtime: Optional[AgentRuntime] = None,
        agent_id: Optional[AgentId] = None,
    ):
        self.name = name
        self.description = description
        self.model_client = model_client
        self.model_context = model_context
        self.tools: List[BaseTool] = list(tools) if tools else []
        self.system_instructions = system_instructions
        self.memory = memory
        self.memory_scope = memory_scope
        self.input_guardrails = input_guardrails or []
        self.output_guardrails = output_guardrails or []
        self.prompt_enricher: Optional[PromptEnricher] = prompt_enricher
        self.runtime: Optional[AgentRuntime] = runtime
        self.agent_id: Optional[AgentId] = agent_id

    def get_effective_system_prompt(self) -> str:
        """Return the system prompt, enriched by prompt_enricher if set."""
        if self.prompt_enricher is not None:
            return self.prompt_enricher.inject_into_prompt(self.system_instructions)
        return self.system_instructions

    # -- Core lifecycle -------------------------------------------------------

    @abstractmethod
    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        """Execute the agent to completion and return a structured result."""
        ...

    @abstractmethod
    def run_stream(self, input_text: str, **kwargs) -> AsyncIterator[Any]:
        """Execute the agent, yielding events/chunks as they happen."""
        ...

    # -- Helpers --------------------------------------------------------------

    async def handle_message(self, ctx: MessageContext, payload: Any) -> Any:
        """Adapter that makes this agent a valid ``MessageHandler``.

        Default implementation calls ``self.run()`` and returns the output.
        Subclasses may override for streaming or custom routing.
        """
        result = await self.run(str(payload))
        return result.output

    async def reset(self) -> None:
        """Clear memory and return agent to initial state."""
        if self.memory:
            await self.memory.clear()

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(name={self.name!r}, tools={len(self.tools)})>"
        )

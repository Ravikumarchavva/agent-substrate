"""Base agent contract.

Every agent type (ReAct, Plan-and-Execute, Custom) implements this interface.
The contract is deliberately minimal:
  - run()        -> full result
  - run_stream() -> async iterator of partial events
"""

from __future__ import annotations

from typing import AsyncIterator, List, Optional, Type, Union
from abc import ABC, abstractmethod
from typing import runtime_checkable, Protocol

from pydantic import BaseModel

from ravi.core.messages.content import JsonValue
from ravi.core.messages._types import StreamChunk
from ravi.core.messages.client_messages import ToolExecutionResultMessage

from ravi.core.agents.agent_result import AgentRunResult
from ravi.core.context.base_context import ModelContext
from ravi.core.tools.base_tool import BaseTool
from ravi.core.catalog import AgentCatalogRegistry
from ravi.core.llm.base_client import BaseModelClient
from ravi.core.memory.base_memory import BaseMemory
from ravi.core.memory.memory_scope import MemoryScope
from ravi.core.execution.context import ExecutionContext
from ravi.core.middleware.base import BaseMiddleware
from ravi.core.middleware.runner import MiddlewarePipeline
from ravi.core.runtime import AgentId, AgentRuntime, MessageContext


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
        catalog: AgentCatalogRegistry,
        system_instructions: str = "You are a helpful assistant.",
        memory_scope: MemoryScope = MemoryScope.ISOLATED,
        prompt_enricher: Optional[PromptEnricher] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        middleware: Optional[List[BaseMiddleware]] = None,
        execution_context: Optional[ExecutionContext] = None,
        runtime: Optional[AgentRuntime] = None,
        agent_id: Optional[AgentId] = None,
    ):
        self.name = name
        self.description = description
        self.catalog = catalog
        # Pull primary resources from the catalog — subclasses ensure these
        # are populated before calling super().__init__().
        self.model_client: Optional[BaseModelClient] = catalog.primary_model()
        self.model_context: Optional[ModelContext] = catalog.primary_context()
        self.memory: Optional[BaseMemory] = catalog.primary_memory()
        self.system_instructions = system_instructions
        self.memory_scope = memory_scope
        self.prompt_enricher: Optional[PromptEnricher] = prompt_enricher or catalog
        self.response_schema: Optional[Type[BaseModel]] = response_schema
        self.middleware_pipeline = MiddlewarePipeline(middleware)
        self.execution_context: Optional[ExecutionContext] = execution_context
        self.runtime: Optional[AgentRuntime] = runtime
        self.agent_id: Optional[AgentId] = agent_id

    @property
    def tools(self) -> List[BaseTool]:
        """Dynamically fetch all tools registered in the unified capability catalog."""
        return self.catalog.all_tools()

    @tools.setter
    def tools(self, value: List[BaseTool]) -> None:
        """Replace tools in the unified capability catalog."""
        # Unregister all current tools
        for t in self.catalog.all_tools():
            self.catalog.unregister(t.name)
        # Register new tools
        for t in value:
            self.catalog.register_tool(t)

    def get_effective_system_prompt(self) -> str:
        """Return the system prompt, enriched by prompt_enricher if set."""
        if self.prompt_enricher is not None:
            return self.prompt_enricher.inject_into_prompt(self.system_instructions)
        return self.system_instructions

    # -- Core lifecycle -------------------------------------------------------

    @abstractmethod
    async def run(
        self,
        input_text: str,
        *,
        response_schema: Optional[Type[BaseModel]] = None,
        **kwargs,
    ) -> AgentRunResult:
        """Execute the agent to completion and return a structured result.

        If ``response_schema`` is provided (or set on the instance), the final
        LLM answer is validated against that Pydantic model and stored in
        ``AgentRunResult.structured_output``.
        """
        ...

    @abstractmethod
    def run_stream(
        self,
        input_text: str,
        *,
        response_schema: Optional[Type[BaseModel]] = None,
        **kwargs,
    ) -> AsyncIterator[Union[StreamChunk, dict, ToolExecutionResultMessage]]:
        """Execute the agent, yielding events/chunks as they happen.

        When ``response_schema`` is set, a ``StructuredOutputChunk`` is yielded
        as the final event after the ``CompletionChunk``.
        """
        ...

    # -- Helpers --------------------------------------------------------------

    async def handle_message(
        self, ctx: MessageContext, payload: JsonValue
    ) -> JsonValue:
        """Adapter that makes this agent a valid ``MessageHandler``.

        Default implementation calls ``self.run()`` and returns the output.
        Subclasses may override for streaming or custom routing.
        """
        result = await self.run(str(payload))
        return result.output

    async def reset(self) -> None:
        """Clear memory and return agent to initial state."""
        if self.memory is not None:
            await self.memory.clear()

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(name={self.name!r}, tools={len(self.tools)})>"
        )

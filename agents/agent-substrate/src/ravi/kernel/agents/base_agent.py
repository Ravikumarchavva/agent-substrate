"""Base agent contract.

Every agent type (ReAct, Plan-and-Execute, Custom) implements this interface.
The contract is deliberately minimal:
  - run()        -> full result
  - run_stream() -> async iterator of partial events
"""

from __future__ import annotations

from typing import AsyncIterator, ClassVar, List, Optional, Type, Union
from abc import ABC, abstractmethod
from typing import runtime_checkable, Protocol

from pydantic import BaseModel

from ravi.kernel.messages.content import JsonValue
from ravi.kernel.messages._types import StreamChunk
from ravi.kernel.messages.client_messages import ToolExecutionResultMessage

from ravi.kernel.agents.agent_result import AgentRunResult
from ravi.kernel.context.base_context import ModelContext
from ravi.kernel.tools.base_tool import BaseTool
from ravi.kernel.agent_catalog import AgentCatalogRegistry
from ravi.kernel.llm.base_client import BaseModelClient
from ravi.kernel.memory.base_memory import BaseMemory
from ravi.kernel.memory.memory_scope import MemoryScope
from ravi.kernel.execution.context import ExecutionContext
from ravi.kernel.middleware.base import BaseMiddleware
from ravi.kernel.middleware.runner import MiddlewarePipeline
from ravi.kernel.runtime import AgentId, AgentRuntime, MessageContext


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

    _DEFAULT_SYSTEM_INSTRUCTIONS: ClassVar[str] = "You are a helpful assistant."

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
        # Pillar D: reject custom system instructions when there is no LLM client.
        if (
            self.model_client is None
            and system_instructions != self._DEFAULT_SYSTEM_INSTRUCTIONS
        ):
            raise ValueError(
                f"Agent {name!r} has no LLM client — "
                "system_instructions may only be customised on LLM-backed agents."
            )
        # Pillar A: private backing store; public attribute is read-only.
        self._system_instructions: str = system_instructions
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

    @property
    def system_instructions(self) -> str:
        """Read-only view of the agent's current system instructions.

        Use ``rewrite_system_prompt()`` (gated by ``MutationPolicy``) to change them.
        """
        return self._system_instructions

    @system_instructions.setter
    def system_instructions(self, value: str) -> None:
        raise AttributeError(
            "system_instructions is read-only. "
            "Call rewrite_system_prompt() — it is gated by MutationPolicy."
        )

    def _update_system_instructions(self, value: str) -> None:
        """Internal write path — only called from rewrite_system_prompt()."""
        self._system_instructions = value

    @abstractmethod
    def get_system_instructions(self) -> str:
        """Return the base system instructions for this agent.

        Every subclass must implement this method and return the current
        system instructions string. Typically ``return self._system_instructions``.
        """

    def get_effective_system_prompt(self) -> str:
        """Return the system prompt, enriched by prompt_enricher if set."""
        base_instructions = self.get_system_instructions()
        if self.prompt_enricher is not None:
            return self.prompt_enricher.inject_into_prompt(base_instructions)
        return base_instructions

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

        Runtimes deliver payloads as ``list[ContentBlock]`` (the universal
        multimodal envelope). This adapter lowers the payload to a flat
        string via :func:`ravi.kernel.messages.content.blocks_to_text` and
        calls ``self.run()``. Plain ``str`` and other shapes are accepted
        as a convenience for in-process callers.

        Subclasses may override for streaming, multimodal, or custom routing.
        """
        from ravi.kernel.messages.content import blocks_to_text

        if isinstance(payload, list):
            input_text = blocks_to_text(payload)
        elif isinstance(payload, str):
            input_text = payload
        else:
            input_text = str(payload)
        result = await self.run(input_text)
        return result.output

    async def reset(self) -> None:
        """Clear memory and return agent to initial state."""
        if self.memory is not None:
            await self.memory.clear()

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(name={self.name!r}, tools={len(self.tools)})>"
        )

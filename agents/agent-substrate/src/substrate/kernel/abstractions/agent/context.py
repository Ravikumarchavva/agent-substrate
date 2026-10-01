"""Agent context, compaction, and prompt window builder contracts."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import Field, model_validator
from typing_extensions import Self

from substrate.kernel.core.content import ChatMessage, ContentBlock, KernelModel
from substrate.kernel.storage.history import HistoryCheckpoint, MessageNode

if TYPE_CHECKING:
    from substrate.kernel.storage.memory import ContextMemoryInjection


class ContextWindow(KernelModel):
    """Immutable assembled context window prepared for LLM generation."""

    messages: list[ChatMessage] = Field(default_factory=list)
    leaf_node_id: str | None = None
    checkpoint_id: str | None = None
    estimated_tokens: int = 0


@runtime_checkable
class ContextBuilder(Protocol):
    """Assembles final LLM-ready prompt windows from resolved DAG nodes."""

    async def build(
        self,
        nodes: Sequence[MessageNode],
        *,
        checkpoint: HistoryCheckpoint | None = None,
        token_budget: int | None = None,
        system_instruction: str | None = None,
        memory_injection: ContextMemoryInjection | None = None,
    ) -> ContextWindow:
        """Combine nodes into a prompt window respecting token budget and turn invariants."""
        ...


@runtime_checkable
class CompactionStrategy(Protocol):
    """Converts raw message history into a manageable LLM context window.

    Implementations might use sliding windows, token truncation, or
    LLM-based summarisation. Input and output are ``list[ChatMessage]``
    — the same type used directly by ``LLMClient.generate``.
    """

    async def compact(self, raw_history: list[ChatMessage]) -> list[ChatMessage]:
        """Return the optimised sequence ready for LLM generation."""
        ...


class CompactionPhase(str, Enum):
    """Execution phases in the agent context lifecycle where compaction can occur."""

    PRE_LLM = "pre_llm"      # Prompt window optimization before generation
    POST_TOOL = "post_tool"  # Tool result truncation/compaction in-memory
    POST_TURN = "post_turn"  # Summary checkpoint generation after turn completes


class CompactionContext(KernelModel):
    """Input parameters provided to compaction strategies for a given phase."""

    session_id: str
    branch_id: str | None = None
    # Context data for PRE_LLM
    messages: list[ChatMessage] = Field(default_factory=list)
    token_budget: int | None = None
    system_instruction: str | None = None
    # Context data for POST_TOOL
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_content: ContentBlock | None = None
    # Context data for POST_TURN
    leaf_node_id: str | None = None
    existing_checkpoint: HistoryCheckpoint | None = None


class CompactionResult(KernelModel):
    """Result of a compaction phase execution, strictly validated against its phase."""

    phase: CompactionPhase
    prompt_messages: list[ChatMessage] | None = None
    compacted_content: ContentBlock | None = None
    checkpoint_proposal: HistoryCheckpoint | None = None

    @model_validator(mode="after")
    def _validate_phase_payload(self) -> Self:
        if self.phase == CompactionPhase.PRE_LLM:
            if self.prompt_messages is None:
                raise ValueError("prompt_messages is required for PRE_LLM phase")
            if self.compacted_content is not None or self.checkpoint_proposal is not None:
                raise ValueError("Only prompt_messages may be set for PRE_LLM phase")
        elif self.phase == CompactionPhase.POST_TOOL:
            if self.compacted_content is None:
                raise ValueError("compacted_content is required for POST_TOOL phase")
            if self.prompt_messages is not None or self.checkpoint_proposal is not None:
                raise ValueError("Only compacted_content may be set for POST_TOOL phase")
        elif self.phase == CompactionPhase.POST_TURN:
            if self.prompt_messages is not None or self.compacted_content is not None:
                raise ValueError("prompt_messages and compacted_content must be None for POST_TURN phase")
        return self


__all__ = [
    "ContextWindow",
    "ContextBuilder",
    "CompactionStrategy",
    "CompactionPhase",
    "CompactionContext",
    "CompactionResult",
]

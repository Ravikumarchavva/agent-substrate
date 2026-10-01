"""LLM client contracts — Protocol definitions only."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, AsyncIterator, Protocol, runtime_checkable

from substrate.kernel.core.content import ChatMessage, ContentBlock, KernelModel, TextBlock
from substrate.kernel.messaging.stream import CompletionEvent, ReasoningDelta, TextDelta
from substrate.kernel.core.usage import Usage

if TYPE_CHECKING:
    from pydantic import BaseModel
    from substrate.kernel.agent.runtime_context import RunMeta
    from substrate.kernel.tools import AnyTool


class Modality(StrEnum):
    """A kind of content an LLM can accept as input or produce as output."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"


class ReasoningEffort(StrEnum):
    """How hard a reasoning-capable model should think before answering.

    ``None`` on ``GenerationOptions.reasoning`` means "provider default".
    Each client maps a level onto its vendor's own control (OpenAI
    ``reasoning.effort``, Anthropic ``thinking.budget_tokens``, Gemini
    ``thinking_budget``) and ignores it for models that don't reason.
    """

    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ModelCapabilities(KernelModel):
    """What a model can accept and what it costs — exposed by every
    ``LLMClient`` as ``client.capabilities``.

    ``input_modalities`` is what the model can actually *see*. Clients drop
    content outside it (replacing it with a text note) before encoding, so
    a text-only model is never sent an image it would reject or silently
    ignore. How a provider transports a modality — e.g. an image inside a
    tool result natively vs. in a follow-up user message — is an encoder
    detail, not a capability.
    """

    model_id: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: frozenset[Modality] = frozenset({Modality.TEXT})
    supports_tool_calling: bool = True
    supports_reasoning: bool = False
    input_cost_per_mtok: float = 0.0
    # None -> cached input is billed at the full input rate (an overestimate,
    # which is the safe direction for a budget).
    cached_input_cost_per_mtok: float | None = None
    output_cost_per_mtok: float = 0.0

    def accepts(self, modality: Modality) -> bool:
        return modality in self.input_modalities

    def cost_usd(self, usage: Usage) -> float:
        cached_rate = (
            self.cached_input_cost_per_mtok
            if self.cached_input_cost_per_mtok is not None
            else self.input_cost_per_mtok
        )
        uncached = max(usage.input_tokens - usage.cached_tokens, 0)
        return (
            uncached * self.input_cost_per_mtok
            + usage.cached_tokens * cached_rate
            + usage.output_tokens * self.output_cost_per_mtok
        ) / 1_000_000


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Return value of ``LLMClient.generate()``."""

    content: list[ContentBlock]
    usage: Usage
    # Priced by the harness from ``LLMClient.capabilities`` — clients leave it 0.
    cost_usd: float = 0.0

    @property
    def text(self) -> str:
        """The concatenated text of every ``TextBlock`` in ``content``.

        Mirrors ``ChatMessage.text``/``ToolResultBlock.text`` — the same
        derived property on the kernel's other content-bearing types.
        """
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    """Typed options bag for LLM generation calls.

    Replaces ``**kwargs`` in ``generate`` and ``generate_stream`` so that:
    - Protocol conformance verifies all meaningful parameters
    - Implementations cannot silently disagree on parameter names
    - Callers get autocomplete and type errors instead of runtime surprises

    ``tools`` is ``list[Tool]`` — the kernel contract. Each LLM client
    converts them to its vendor wire-format internally.

    ``system_instructions`` is the system prompt text.
    """

    tools: list["AnyTool"] | None = None
    system_instructions: str = ""
    temperature: float | None = None
    max_tokens: int | None = None
    tool_choice: str | dict | None = None
    response_format: "type[BaseModel] | None" = None
    stop: list[str] | None = None
    reasoning: ReasoningEffort | None = None
    extra: dict = field(default_factory=dict)


@runtime_checkable
class LLMClient(Protocol):
    """Contract every LLM provider adapter must satisfy.

    ``capabilities`` describes what the model can see and what it costs. A
    client must never send content outside ``capabilities.input_modalities``
    to its provider.
    """

    model: str
    capabilities: ModelCapabilities

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
        ctx: RunMeta | None = None,
    ) -> LLMResponse: ...

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
        ctx: RunMeta | None = None,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
        """Return an async iterator of token events.

        ``ctx`` carries the cancellation token and deadline.  Implementations
        should call ``ctx.check()`` before the first I/O call and honour
        ``ctx.is_expired()`` between streamed chunks.

        Implementations should be async generator functions (``async def … yield``),
        which are synchronous callables that return an ``AsyncIterator``.  Callers
        use ``async for event in model.generate_stream(messages, options=opts):``.
        No ``await`` is needed before the ``async for``.
        """
        ...

    async def count_tokens(self, messages: list[ChatMessage]) -> int: ...


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Return value of ``EmbeddingClient.embed()``."""

    embeddings: list[list[float]]
    model: str
    usage_tokens: int = 0


@runtime_checkable
class EmbeddingClient(Protocol):
    """Contract every embedding provider adapter must satisfy."""

    async def embed(self, texts: list[str]) -> EmbeddingResult: ...

    async def embed_single(self, text: str) -> list[float]: ...

    async def embed_blocks(self, blocks: Sequence[ContentBlock]) -> list[float]: ...


__all__ = [
    "GenerationOptions",
    "LLMClient",
    "LLMResponse",
    "EmbeddingClient",
    "EmbeddingResult",
    "Usage",
    "Modality",
    "ReasoningEffort",
    "ModelCapabilities",
]

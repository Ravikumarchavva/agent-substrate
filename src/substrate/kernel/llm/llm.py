"""LLM client contracts — Protocol definitions only."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, AsyncIterator, Protocol, runtime_checkable

from pydantic import Field

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


class ModelCapabilities(KernelModel):
    """What a specific model supports — for routing, not for calling it.

    A shared type so a router (once ``fabric/`` has one) can pick between
    models generically instead of every call site hardcoding per-provider
    knowledge — mirrors the ad-hoc modality/audio-support flags that
    already exist per-provider in ``agents/llm/models.py``, promoted here
    since routing on capability is a genuinely cross-layer concern. No
    consumer builds a router against this yet; this is the contract for
    when one does.
    """

    model_id: str
    context_window: int
    max_output_tokens: int | None = None
    input_modalities: list[Modality] = Field(default_factory=list)
    output_modalities: list[Modality] = Field(default_factory=list)
    supports_tool_calling: bool = False
    supports_streaming: bool = False
    cost_per_input_token_usd: float | None = None
    cost_per_output_token_usd: float | None = None


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Return value of ``LLMClient.generate()``."""

    content: list[ContentBlock]
    usage: Usage

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
    extra: dict = field(default_factory=dict)


@runtime_checkable
class LLMClient(Protocol):
    """Contract every LLM provider adapter must satisfy.

    A client MAY additionally expose a ``capabilities: ModelCapabilities |
    None`` attribute for callers that want to introspect context window,
    modality support, or cost — deliberately not part of this Protocol's
    required structural surface (so existing clients aren't forced to add
    it, and ``isinstance(x, LLMClient)`` keeps working for every current
    implementation); no router in this codebase consults it yet.
    """

    model: str

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
    "ModelCapabilities",
]

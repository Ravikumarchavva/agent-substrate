"""LLM client contracts — Protocol definitions only."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncIterator, Protocol

from ravi.kernel.content import ChatMessage, ContentBlock
from ravi.kernel.stream import CompletionEvent, ReasoningDelta, TextDelta
from ravi.kernel.usage import Usage

if TYPE_CHECKING:
    from pydantic import BaseModel
    from ravi.kernel.tools import Tool


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Return value of ``LLMClient.generate()``."""

    content: list[ContentBlock]
    usage: Usage


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

    tools: list["Tool"] | None = None
    system_instructions: str = ""
    temperature: float | None = None
    max_tokens: int | None = None
    tool_choice: str | dict | None = None
    response_format: "type[BaseModel] | None" = None
    stop: list[str] | None = None
    extra: dict = field(default_factory=dict)


class LLMClient(Protocol):
    """Contract every LLM provider adapter must satisfy."""

    model: str

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse: ...

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
        """Return an async iterator of token events.

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


class EmbeddingClient(Protocol):
    """Contract every embedding provider adapter must satisfy."""

    async def embed(self, texts: list[str]) -> EmbeddingResult: ...

    async def embed_single(self, text: str) -> list[float]: ...



__all__ = [
    "GenerationOptions",
    "LLMClient",
    "LLMResponse",
    "EmbeddingClient",
    "EmbeddingResult",
    "Usage",
]

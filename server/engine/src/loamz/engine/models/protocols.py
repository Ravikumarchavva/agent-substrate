"""What storage needs from a model: an embedder (text, or text and images, to vectors) and a reranker.

Chat models are not here: storage never generates text. Loamz Agents' model ports extend these.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from loamz.engine.types import ContentBlock


class Modality(StrEnum):
    """A kind of content a model takes."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """What ``EmbeddingModel.embed()`` returns: one vector per input, in input order."""

    embeddings: list[list[float]]
    model: str
    usage_tokens: int = 0


@runtime_checkable
class EmbeddingModel(Protocol):
    """One method, and four facts about the model.

    ``dimensions`` is the vector width (``None`` until a remote model has said, or has been asked once);
    ``max_input_tokens`` the longest input it takes (a chunker sizes to it); ``modalities`` what it can embed.

    ``embed`` returns exactly one vector per input, in order, all the same width, and **raises**
    (``ServiceUnavailableError``, ``UnauthenticatedError``, ``RateLimitedError``, ``ContextLengthError``) rather than
    returning a partial answer. A block the model cannot embed raises ``UnsupportedContentError``. ``query=True`` says
    the inputs are search queries, for a model that embeds a query differently from a passage.
    """

    model: str
    dimensions: int | None
    max_input_tokens: int
    modalities: frozenset[Modality]

    async def embed(
        self, inputs: Sequence[str | Sequence[ContentBlock]], *, query: bool = False
    ) -> EmbeddingResult: ...


@runtime_checkable
class Reranker(Protocol):
    """How relevant each passage is to a query: one score per passage, in order, higher is more relevant.

    Scores are comparable within a call, not across calls. Raises a typed error, as ``EmbeddingModel.embed`` does.
    """

    model: str

    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]: ...


__all__ = ["EmbeddingModel", "EmbeddingResult", "Modality", "Reranker"]

"""Document chunking strategies for RAG ingestion.

All chunkers produce ``Document`` objects ready for embedding and storage.

Usage::

    from ravi.catalog.rag.chunking import TextChunker, SentenceChunker

    chunker = TextChunker(chunk_size=512, overlap=128)
    docs = chunker.chunk("Long text ...", metadata={"source": "readme.md"})
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from ravi.catalog.rag.vector_store import Document


class TextChunker:
    """Fixed-size character chunker with overlap.

    Splits text into chunks of ``chunk_size`` characters with
    ``overlap`` characters of overlap between consecutive chunks.
    """

    def __init__(self, chunk_size: int = 512, overlap: int = 128) -> None:
        if overlap >= chunk_size:
            raise ValueError("overlap must be less than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(
        self,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> list[Document]:
        metadata = metadata or {}
        docs: list[Document] = []
        start = 0

        while start < len(text):
            end = start + self.chunk_size
            chunk_text = text[start:end].strip()
            if chunk_text:
                docs.append(
                    Document(
                        text=chunk_text,
                        metadata={**metadata, "chunk_index": len(docs)},
                        id=str(uuid.uuid4()),
                    )
                )
            start += self.chunk_size - self.overlap

        return docs


class SentenceChunker:
    """Sentence-boundary chunker.

    Groups sentences into chunks that don't exceed ``max_chunk_size``
    characters.  Sentence boundaries are detected with a simple regex.
    """

    _SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

    def __init__(self, max_chunk_size: int = 512) -> None:
        self.max_chunk_size = max_chunk_size

    def chunk(
        self,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> list[Document]:
        metadata = metadata or {}
        sentences = self._SENTENCE_RE.split(text)
        docs: list[Document] = []
        current: list[str] = []
        current_len = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if current_len + len(sentence) > self.max_chunk_size and current:
                docs.append(
                    Document(
                        text=" ".join(current),
                        metadata={**metadata, "chunk_index": len(docs)},
                        id=str(uuid.uuid4()),
                    )
                )
                current = []
                current_len = 0
            current.append(sentence)
            current_len += len(sentence) + 1  # +1 for space

        if current:
            docs.append(
                Document(
                    text=" ".join(current),
                    metadata={**metadata, "chunk_index": len(docs)},
                    id=str(uuid.uuid4()),
                )
            )

        return docs


class PageChunker:
    """Page-level chunker for pre-segmented documents (e.g. PDF pages).

    Each page becomes a separate ``Document`` with ``page_number`` metadata.
    """

    def chunk(
        self,
        pages: list[str],
        metadata: Optional[dict[str, Any]] = None,
    ) -> list[Document]:
        metadata = metadata or {}
        docs: list[Document] = []

        for i, page_text in enumerate(pages):
            page_text = page_text.strip()
            if page_text:
                docs.append(
                    Document(
                        text=page_text,
                        metadata={**metadata, "page_number": i + 1},
                        id=str(uuid.uuid4()),
                    )
                )

        return docs


# ── Factory ───────────────────────────────────────────────────────────────────

_CHUNKERS = {
    "text": TextChunker,
    "sentence": SentenceChunker,
}


def get_chunker(
    name: str = "text",
    **kwargs: Any,
) -> TextChunker | SentenceChunker:
    """Get a chunker by name."""
    cls = _CHUNKERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown chunker: {name!r}. Available: {list(_CHUNKERS)}")
    return cls(**kwargs)

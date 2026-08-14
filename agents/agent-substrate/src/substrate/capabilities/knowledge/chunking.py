"""Document chunking strategies for RAG ingestion.

All chunkers produce ``Document`` objects ready for embedding and storage.

Usage::

    from substrate.capabilities.knowledge.chunking import TextChunker, SentenceChunker

    chunker = TextChunker(chunk_size=512, overlap=128)
    docs = chunker.chunk("Long text ...", metadata={"source": "readme.md"})
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from substrate.kernel.core.content import TextBlock
from substrate.kernel.storage.vector import Document


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
        metadata: dict[str, Any] | None = None,
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
                        content=[TextBlock(text=chunk_text)],
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
        metadata: dict[str, Any] | None = None,
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
                        content=[TextBlock(text=" ".join(current))],
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
                    content=[TextBlock(text=" ".join(current))],
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
        metadata: dict[str, Any] | None = None,
    ) -> list[Document]:
        metadata = metadata or {}
        docs: list[Document] = []

        for i, page_text in enumerate(pages):
            page_text = page_text.strip()
            if page_text:
                docs.append(
                    Document(
                        content=[TextBlock(text=page_text)],
                        metadata={**metadata, "page_number": i + 1},
                        id=str(uuid.uuid4()),
                    )
                )

        return docs


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


class StructureAwareChunker:
    """Markdown-heading-aware chunker with sentence packing and section linkage.

    Splits text into sections on markdown headings (``#`` .. ``######``, adapted
    from ``PageIndexRAGPipeline._build_markdown_tree``'s header-detection
    approach). Text with no detected headings falls back to a single section
    covering the whole input. Within each section, sentences are packed
    greedily (reusing ``SentenceChunker._SENTENCE_RE``) up to ``chunk_size``
    characters with ``overlap`` characters carried over between consecutive
    chunks.

    Protected spans: callers may pass ``protected_spans`` to :meth:`chunk` —
    a list of ``(start_offset, end_offset)`` character offsets into the
    *original* ``text`` (e.g. detected table/image-caption blocks) that must
    never be split. Any such span is emitted verbatim as its own chunk,
    regardless of ``chunk_size``, and sentence packing resumes around it.

    Every returned ``Document`` carries three extra metadata fields set in
    document order across the whole input: ``prev_chunk_id`` / ``next_chunk_id``
    (neighbouring chunk ids, ``None`` at the ends) and ``section_id`` (an
    incrementing int shared by every chunk produced from the same detected
    section).
    """

    _SENTENCE_RE = SentenceChunker._SENTENCE_RE

    def __init__(self, chunk_size: int = 512, overlap: int = 128) -> None:
        if overlap >= chunk_size:
            raise ValueError("overlap must be less than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        protected_spans: list[tuple[int, int]] | None = None,
    ) -> list[Document]:
        metadata = metadata or {}
        protected_spans = self._merge_spans(protected_spans or [])

        section_texts: list[str] = []
        chunk_section_ids: list[int] = []
        section_id = 0

        for start, end, _title in self._detect_sections(text):
            section_chunks = self._pack_section(text, start, end, protected_spans)
            if not section_chunks:
                continue
            section_texts.extend(section_chunks)
            chunk_section_ids.extend([section_id] * len(section_chunks))
            section_id += 1

        docs: list[Document] = []
        for i, chunk_text in enumerate(section_texts):
            docs.append(
                Document(
                    content=[TextBlock(text=chunk_text)],
                    metadata={
                        **metadata,
                        "chunk_index": i,
                        "section_id": chunk_section_ids[i],
                    },
                    id=str(uuid.uuid4()),
                )
            )

        for i, doc in enumerate(docs):
            doc.metadata["prev_chunk_id"] = docs[i - 1].id if i > 0 else None
            doc.metadata["next_chunk_id"] = (
                docs[i + 1].id if i < len(docs) - 1 else None
            )

        return docs

    # ── Section detection ────────────────────────────────────────────────

    def _detect_sections(self, text: str) -> list[tuple[int, int, str]]:
        """Split ``text`` into ``(start, end, title)`` spans on markdown headings.

        ``start``/``end`` exclude the heading line itself. Falls back to a
        single ``(0, len(text), "")`` section when no heading is detected.
        """
        headings: list[dict[str, Any]] = []
        offset = 0
        for line in text.splitlines(keepends=True):
            match = _HEADING_RE.match(line.rstrip("\n"))
            if match:
                headings.append(
                    {
                        "start": offset,
                        "content_start": offset + len(line),
                        "title": match.group(2).strip(),
                    }
                )
            offset += len(line)

        if not headings:
            return [(0, len(text), "")]

        sections: list[tuple[int, int, str]] = []
        if headings[0]["start"] > 0:
            sections.append((0, headings[0]["start"], ""))
        for i, heading in enumerate(headings):
            end = headings[i + 1]["start"] if i + 1 < len(headings) else len(text)
            sections.append((heading["content_start"], end, heading["title"]))

        return [s for s in sections if text[s[0] : s[1]].strip()]

    # ── Protected spans ──────────────────────────────────────────────────

    @staticmethod
    def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
        merged: list[tuple[int, int]] = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    # ── Packing ──────────────────────────────────────────────────────────

    def _pack_section(
        self,
        text: str,
        start: int,
        end: int,
        protected_spans: list[tuple[int, int]],
    ) -> list[str]:
        section_text = text[start:end]
        rel_spans = [
            (max(s, start) - start, min(e, end) - start)
            for s, e in protected_spans
            if s < end and e > start
        ]

        pieces: list[tuple[bool, str]] = []
        cursor = 0
        for s, e in rel_spans:
            if s > cursor:
                pieces.append((False, section_text[cursor:s]))
            pieces.append((True, section_text[s:e]))
            cursor = e
        if cursor < len(section_text):
            pieces.append((False, section_text[cursor:]))
        if not pieces:
            pieces = [(False, section_text)]

        chunks: list[str] = []
        for is_atomic, piece_text in pieces:
            if not piece_text.strip():
                continue
            if is_atomic:
                chunks.append(piece_text.strip())
            else:
                chunks.extend(self._pack_sentences(piece_text))
        return chunks

    def _pack_sentences(self, text: str) -> list[str]:
        sentences = [s.strip() for s in self._SENTENCE_RE.split(text) if s.strip()]
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        i = 0

        while i < len(sentences):
            sentence = sentences[i]
            if current_len + len(sentence) + 1 > self.chunk_size and current:
                chunks.append(" ".join(current))
                overlap_sentences: list[str] = []
                overlap_len = 0
                for s in reversed(current):
                    if overlap_len + len(s) + 1 > self.overlap:
                        break
                    overlap_sentences.insert(0, s)
                    overlap_len += len(s) + 1
                current = overlap_sentences
                current_len = overlap_len
                continue
            current.append(sentence)
            current_len += len(sentence) + 1
            i += 1

        if current:
            chunks.append(" ".join(current))

        return chunks


# ── Factory ───────────────────────────────────────────────────────────────────

_CHUNKERS = {
    "text": TextChunker,
    "sentence": SentenceChunker,
    "structure": StructureAwareChunker,
}


def get_chunker(
    name: str = "text",
    **kwargs: Any,
) -> TextChunker | SentenceChunker | StructureAwareChunker:
    """Get a chunker by name."""
    cls = _CHUNKERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown chunker: {name!r}. Available: {list(_CHUNKERS)}")
    return cls(**kwargs)

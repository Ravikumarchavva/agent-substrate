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
from html.parser import HTMLParser
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

# Matches a raw HTML <table>...</table> block — document-intelligence's
# extraction markdown embeds some detected tables this way (ones not
# confident enough to be cropped as a separate image; see
# runtimes/document_intelligence/service/pipeline.py's _IMAGE_LABELS/
# confidence gate). An HTML table has ~no ". "/"! "/"? " boundaries, so
# SentenceChunker's regex sees it as one giant unsplittable "sentence" —
# _split_oversized_table below is what actually breaks it up.
_TABLE_RE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)


class _TableRowParser(HTMLParser):
    """Minimal ``<table>`` row/cell extractor — same technique as
    document_intelligence's own ``_TableHTMLToMarkdown`` (not imported
    directly: that lives in the ``runtimes/`` layer, which ``capabilities/``
    must not depend on — see this repo's layered-architecture rules).
    Stdlib-only, no new dependency; spans/nested tables are not
    reconstructed, matching that same precedent's documented limitation."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _rows_to_markdown(rows: list[list[str]]) -> str:
    """Render row groups as a compact GFM markdown table — more
    token-efficient than re-emitting HTML tags, which is all that matters
    once this is headed for an embedding call, not a renderer."""
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]
    lines = [
        "| " + " | ".join(padded[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in padded[1:]]
    return "\n".join(lines)


def _split_oversized_table(html: str, max_size: int) -> list[str]:
    """Split an HTML ``<table>`` too large to embed as one chunk into
    row-group pieces, each rendered as its own markdown table with the
    header row repeated — so every piece stays independently meaningful,
    not an arbitrary character split through the middle of a row. Falls
    back to returning ``[html]`` unchanged if parsing yields fewer than 2
    rows (nothing to split) or fails outright — the caller's existing
    skip-on-embed-failure handling covers that case same as before this
    function existed.
    """
    parser = _TableRowParser()
    try:
        parser.feed(html)
    except Exception:
        return [html]
    rows = parser.rows
    if len(rows) < 2:
        return [html]

    header, body = rows[0], rows[1:]
    pieces: list[str] = []
    current = [header]
    for row in body:
        candidate = current + [row]
        rendered = _rows_to_markdown(candidate)
        if len(rendered) > max_size and len(current) > 1:
            pieces.append(_rows_to_markdown(current))
            current = [header, row]
        else:
            current = candidate
    if len(current) > 1:
        pieces.append(_rows_to_markdown(current))
    return pieces or [html]


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

        sections = self._merge_tiny_sections(text, self._detect_sections(text))
        for start, end, title in sections:
            section_chunks = self._pack_section(
                text, start, end, protected_spans, title=title
            )
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

    _MIN_SECTION_BODY_CHARS = 20

    def _merge_tiny_sections(
        self, text: str, sections: list[tuple[int, int, str]]
    ) -> list[tuple[int, int, str]]:
        """Fold a section whose own body is too short to carry useful
        meaning on its own (e.g. a stray OCR fragment, or two headings
        with almost nothing detected between them) into its neighbor,
        rather than emitting it as a standalone near-empty chunk. Real,
        found-not-assumed: seen directly in production output — a whole
        chunk containing only ``"NEW K"`` (5 characters), from real
        extracted markdown. Merges forward into the following section so
        the fragment stays adjacent to what follows it in the document;
        the last section merges backward since there's no "next". The
        threshold (20 chars) is well below any legitimate short paragraph
        (a one-sentence section already runs 40+ chars) so it only ever
        catches genuine fragments, not real short sections.
        """
        if len(sections) <= 1:
            return sections

        sections = list(sections)
        result: list[tuple[int, int, str]] = []
        i = 0
        n = len(sections)
        while i < n:
            start, end, title = sections[i]
            body_len = len(text[start:end].strip())
            if body_len < self._MIN_SECTION_BODY_CHARS and i + 1 < n:
                next_start, next_end, next_title = sections[i + 1]
                combined_title = " — ".join(t for t in (title, next_title) if t)
                sections[i + 1] = (start, next_end, combined_title)
                i += 1
                continue
            if body_len < self._MIN_SECTION_BODY_CHARS and result:
                prev_start, prev_end, prev_title = result[-1]
                combined_title = " — ".join(t for t in (prev_title, title) if t)
                result[-1] = (prev_start, end, combined_title)
                i += 1
                continue
            result.append((start, end, title))
            i += 1
        return result

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
        *,
        title: str = "",
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

        # Carry the section's own heading into its first chunk rather than
        # dropping it — real, found-not-assumed: a body's first sentence
        # is often the predicate of a subject named only in its heading
        # ("## The Higg Index" / "is a suite of tools used widely..."),
        # and without the heading that chunk reads as an orphaned sentence
        # with no antecedent, which hurts both embedding quality and a
        # human skimming citations. Small, bounded overflow past
        # chunk_size is an accepted tradeoff here, same as protected spans
        # above (see this class's own docstring).
        if title:
            if chunks:
                chunks[0] = f"{title}\n\n{chunks[0]}"
            else:
                chunks = [title]
        return chunks

    def _pack_sentences(self, text: str) -> list[str]:
        sentences = [s.strip() for s in self._SENTENCE_RE.split(text) if s.strip()]
        sentences = self._expand_oversized_tables(sentences)
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
                if len(overlap_sentences) == len(current):
                    # Overlap window retained every sentence from the flushed
                    # chunk (none were trimmed) — carrying it forward would
                    # reproduce the exact same `current`, and if the next
                    # sentence alone still doesn't fit, the loop would flush
                    # this identical chunk forever without `i` advancing.
                    # Drop the overlap here so `current` strictly shrinks.
                    overlap_sentences = []
                    overlap_len = 0
                current = overlap_sentences
                current_len = overlap_len
                continue
            current.append(sentence)
            current_len += len(sentence) + 1
            i += 1

        if current:
            chunks.append(" ".join(current))

        return chunks

    def _expand_oversized_tables(self, sentences: list[str]) -> list[str]:
        """Break any "sentence" that's really an over-``chunk_size`` HTML
        ``<table>`` (see ``_TABLE_RE``'s comment) into several smaller
        table pieces via ``_split_oversized_table``, so packing sees
        several embeddable-sized entries instead of one unsplittable
        oversized one. Non-table sentences, and tables that already fit,
        pass through unchanged.
        """
        expanded: list[str] = []
        for s in sentences:
            if len(s) <= self.chunk_size:
                expanded.append(s)
                continue
            match = _TABLE_RE.search(s)
            if not match:
                expanded.append(s)
                continue
            before, table_html, after = (
                s[: match.start()],
                match.group(0),
                s[match.end() :],
            )
            if before.strip():
                expanded.append(before.strip())
            expanded.extend(_split_oversized_table(table_html, self.chunk_size))
            if after.strip():
                expanded.append(after.strip())
        return expanded


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

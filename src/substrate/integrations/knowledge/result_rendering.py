"""Shared rendering of retrieval results into a cited ``ToolExecutionResult``.

Both ``KnowledgeSearchTool`` (project knowledge base) and
``SessionDocumentSearchTool`` (a user's chat-uploaded documents) need the
same thing done to a list of ``SearchResult``s: number them with stable,
clickable citations (``capabilities/knowledge/citations.py``), attach
chart/table images without re-sending ones already shown earlier in the
conversation, and format the passages into one text block the model can
reason over and cite ``[n]`` from. ``render_search_results`` is that shared
path, so numbered citations/reranking/image-attachment aren't reimplemented
per-tool.
"""

from __future__ import annotations

from substrate.integrations.knowledge.citations import CitationLedger, build_citations
from substrate.kernel import MediaBlock, TextBlock
from substrate.kernel.storage.vector import SearchResult
from substrate.kernel.tools import ToolExecutionResult


def render_search_results(
    results: list[SearchResult],
    *,
    backend_name: str,
    collection: str,
    ledger: CitationLedger,
    query_text: str,
    min_score: float = 0.1,
) -> ToolExecutionResult:
    """Turn raw retrieval *results* into a labelled, cited tool result.

    Retrieval itself (``backend.query(...)``) stays a caller concern —
    different tools/modes retrieve differently — but everything after that
    (citation numbering, image dedup, text formatting) is identical across
    callers, hence shared here.
    """
    if not results:
        return ToolExecutionResult(
            content=[TextBlock(text="No matching documents found.")],
        )

    cited = build_citations(
        results,
        backend_name=backend_name,
        collection=collection,
        ledger=ledger,
        min_score=min_score,
    )
    citation_by_index = {c.index: c for c in cited.citations}
    # Full passages, not search-engine-style snippets — the model
    # reasons over this text directly, so truncating it hard (this
    # used to cut to 200 chars) starves it of the detail needed for
    # a specific, confident answer even when retrieval found the
    # right chunk. Each passage is labelled with its citation number
    # so the model can cite [n] — see ATTACHMENT_ANALYSIS_INSTRUCTIONS
    # in routes/chat_intents.py for how it's told to use this.
    lines = [f"Top {len(results)} results for '{query_text}':"]
    image_blocks: list[MediaBlock] = []
    for i, result in enumerate(results):
        index = cited.index_for[i]
        citation = citation_by_index.get(index)
        label = f"[{index}] {citation.label()}" if citation else "(unlabelled)"
        # A chart/table hit's content IS the image — forward the real
        # MediaBlock into the tool result (same path
        # capabilities/tools/ai/image_generator.py already uses) so a
        # vision-capable model sees the actual pixels, not just OCR
        # text of it.
        #
        # Attach each image at most once per conversation. A document
        # usually holds only a handful of chart/table images, so every
        # search in a turn retrieves the *same* top-k images — attaching
        # them each time re-sent identical pixels to the model and made
        # the UI render the same "N charts generated" group once per
        # call. `first_seen` comes from the citation ledger, which
        # already tracks per-(file, page) novelty for the life of the
        # collection, so this also covers repeats within one batch.
        page_images = [b for b in result.content if isinstance(b, MediaBlock) and b.is_image]
        is_new = cited.first_seen[i] if i < len(cited.first_seen) else True
        if is_new:
            image_blocks.extend(page_images)
        if page_images and not any(
            True for b in result.content if not (isinstance(b, MediaBlock) and b.is_image)
        ):
            note = (
                "[see attached image]"
                if is_new
                else "[image already attached earlier in this conversation]"
            )
            lines.append(f"\n{label} (score: {result.score:.3f})\n{note}")
            continue
        passage = result.to_text()[:4000]
        lines.append(f"\n{label} (score: {result.score:.3f})\n{passage}")
    return ToolExecutionResult(
        content=[TextBlock(text="\n".join(lines)), *image_blocks],
        structured_content=cited.to_wire() if cited.citations else {},
    )


__all__ = ["render_search_results"]

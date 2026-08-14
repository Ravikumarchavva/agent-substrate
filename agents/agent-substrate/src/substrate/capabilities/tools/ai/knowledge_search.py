"""KnowledgeSearchTool — semantic search over a real knowledge base.

Thin wrapper around a ``RagBackend`` (``capabilities/knowledge/backends/``) —
all ingestion/retrieval logic lives there (local pgvector pipeline, or a
managed service like Pinecone Assistant). This tool only adapts the agent
tool-call shape to ``backend.ingest``/``backend.query``, and labels each
retrieved passage with a stable citation number (``capabilities/knowledge/
citations.py``) so the model can cite ``[n]`` and the UI can render a
clickable, grounded source for it.
"""

from __future__ import annotations

from substrate.agents.storage.tasks import current_thread_id
from substrate.capabilities.knowledge.backends import RagBackend
from substrate.capabilities.knowledge.citations import (
    CitationLedgerStore,
    build_citations,
)
from substrate.kernel import ImageBlock, TextBlock
from substrate.kernel.tools import ToolExecutionResult, ToolType
from substrate.logger import setup_logging

logger = setup_logging()


class KnowledgeSearchTool:
    """Search or ingest into a knowledge base via a ``RagBackend``."""

    tool_type = ToolType.KNOWLEDGE
    name: str = "knowledge_search"
    description: str = (
        "Search or ingest into the project's knowledge base. "
        "action=search: retrieve passages relevant to a query. "
        "action=ingest: index a document's text. "
        "Page navigation: after a search result names a file_id and "
        "page_number (see its label), pass those back on a follow-up search "
        "to jump straight to a specific page of a specific file instead of "
        "searching by similarity again — e.g. to read the page right after "
        "a match that looked cut off."
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "ingest"],
                "description": "Operation to perform on the knowledge base.",
            },
            "text": {
                "type": "string",
                "description": "Document text to ingest, or query text to search.",
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Max results to return (search action, default 5, max 20). "
                    "Use 10-15 for broad requests like summarizing a whole "
                    "document — the default under-covers multi-section docs."
                ),
            },
            "file_id": {
                "type": "string",
                "description": (
                    "search action only. Restrict results to one file "
                    "(from a prior result's citation metadata) — pair with "
                    "page_number for explicit page navigation."
                ),
            },
            "page_number": {
                "type": "integer",
                "description": (
                    "search action only. Restrict results to one page of "
                    "the file named by file_id."
                ),
            },
        },
        "required": ["action", "text"],
        "additionalProperties": False,
    }

    def __init__(self, backend: RagBackend, *, collection: str = "default") -> None:
        self._backend = backend
        self._default_collection = collection
        # One ledger per collection (chat thread), held for the tool's
        # lifetime — init_tool_registry runs once in lifespan, so this
        # instance is process-wide and citation numbers stay stable across
        # every knowledge_search call in a conversation. See citations.py.
        self._ledgers = CitationLedgerStore()

    def _collection(self) -> str:
        # Scope to the active chat thread when running inside a ReActAgent
        # (stamped by agents/core/react.py::ReActAgent._handle_message, same
        # ContextVar TaskManagerTool uses) — one user's uploaded docs stay
        # invisible to every other thread's knowledge_search calls. Falls
        # back to the constructor default outside a chat context.
        return current_thread_id.get() or self._default_collection

    async def execute(
        self,
        *,
        action: str,
        text: str = "",
        limit: int = 5,
        file_id: str = "",
        page_number: int | None = None,
        **_: object,
    ) -> ToolExecutionResult:
        limit = max(1, min(limit, 20))

        if not text.strip():
            return ToolExecutionResult(
                content=[TextBlock(text="'text' is required.")],
                is_error=True,
            )

        collection = self._collection()

        if action == "ingest":
            result = await self._backend.ingest(text, collection=collection)
            suffix = (
                f"{result.chunks_indexed} chunks"
                if result.chunks_indexed >= 0
                else "document"
            )
            return ToolExecutionResult(
                content=[TextBlock(text=f"Indexed {suffix} into the knowledge base.")],
            )

        if action == "search":
            filter_: dict[str, object] = {}
            if file_id:
                filter_["file_id"] = file_id
            if page_number is not None:
                filter_["page_number"] = page_number
            results = await self._backend.query(
                text, collection=collection, limit=limit, filter=filter_ or None
            )
            if not results:
                return ToolExecutionResult(
                    content=[TextBlock(text="No matching documents found.")],
                )
            cited = build_citations(
                results,
                backend_name=self._backend.name,
                collection=collection,
                ledger=self._ledgers.get(collection),
            )
            citation_by_index = {c.index: c for c in cited.citations}
            # Full passages, not search-engine-style snippets — the model
            # reasons over this text directly, so truncating it hard (this
            # used to cut to 200 chars) starves it of the detail needed for
            # a specific, confident answer even when retrieval found the
            # right chunk. Each passage is labelled with its citation number
            # so the model can cite [n] — see ATTACHMENT_ANALYSIS_INSTRUCTIONS
            # in routes/chat_intents.py for how it's told to use this.
            lines = [f"Top {len(results)} results for '{text}':"]
            image_blocks: list[ImageBlock] = []
            for i, result in enumerate(results):
                index = cited.index_for[i]
                citation = citation_by_index.get(index)
                label = f"[{index}] {citation.label()}" if citation else "(unlabelled)"
                # A chart/table hit's content IS the image — forward the real
                # ImageBlock into the tool result (same path
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
                page_images = [b for b in result.content if isinstance(b, ImageBlock)]
                is_new = cited.first_seen[i] if i < len(cited.first_seen) else True
                if is_new:
                    image_blocks.extend(page_images)
                if page_images and not any(
                    True for b in result.content if not isinstance(b, ImageBlock)
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

        return ToolExecutionResult(
            content=[TextBlock(text=f"Unknown action: {action!r}")],
            is_error=True,
        )

"""KnowledgeSearchTool — semantic search over a real knowledge base.

Thin wrapper around a ``RagBackend`` (``capabilities/knowledge/backends/``) —
all ingestion/retrieval logic lives there (``LocalRagBackend``'s pgvector
pipeline today). This tool only adapts the agent
tool-call shape to ``backend.ingest``/``backend.query``, and labels each
retrieved passage with a stable citation number (``capabilities/knowledge/
citations.py``) so the model can cite ``[n]`` and the UI can render a
clickable, grounded source for it.
"""

from __future__ import annotations

from substrate.agents.storage.tasks import current_thread_id
from substrate.integrations.knowledge.backends import RagBackend
from substrate.integrations.knowledge.citations import CitationLedgerStore
from substrate.integrations.knowledge.result_rendering import render_search_results
from substrate.kernel import TextBlock
from substrate.kernel.tools import ToolExecutionResult, ToolType
from substrate.logger import setup_logging

logger = setup_logging()


class KnowledgeSearchTool:
    """Search or ingest into a knowledge base via a ``RagBackend``."""

    tool_type = ToolType.KNOWLEDGE
    name: str = "knowledge_search"
    description: str = (
        "Search or ingest into the project's STANDING knowledge base — "
        "curated documents shared across every user, not anything from "
        "this chat. For a file the user just uploaded or attached in this "
        "conversation (or their other recent chats), use "
        "session_document_search instead — that tool, not this one, "
        "searches what they actually attached. "
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

    def __init__(
        self,
        backend: RagBackend,
        *,
        collection: str = "default",
        final_k: int = 5,
        min_rerank_score: float = 0.1,
    ) -> None:
        self._backend = backend
        self._default_collection = collection
        # Mirrors config.py's RAG_FINAL_K/RAG_MIN_RERANK_SCORE — the
        # server wires the real config values in via serving_factory.py;
        # these defaults are library defaults for direct/non-server use.
        self._final_k = final_k
        self._min_rerank_score = min_rerank_score
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
        limit: int | None = None,
        file_id: str = "",
        page_number: int | None = None,
        **_: object,
    ) -> ToolExecutionResult:
        limit = max(1, min(limit if limit is not None else self._final_k, 20))

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
            return render_search_results(
                results,
                backend_name=self._backend.name,
                collection=collection,
                ledger=self._ledgers.get(collection),
                query_text=text,
                min_score=self._min_rerank_score,
            )

        return ToolExecutionResult(
            content=[TextBlock(text=f"Unknown action: {action!r}")],
            is_error=True,
        )

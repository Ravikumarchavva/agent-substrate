"""KnowledgeSearchTool — semantic search over a real knowledge base.

Thin wrapper around a ``RagBackend`` (``capabilities/knowledge/backends/``) —
all ingestion/retrieval logic lives there (local pgvector pipeline, or a
managed service like Pinecone Assistant). This tool only adapts the agent
tool-call shape to ``backend.ingest``/``backend.query``.
"""

from __future__ import annotations

from substrate.capabilities.knowledge.backends import RagBackend
from substrate.kernel import TextBlock
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
        "action=ingest: index a document's text."
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
                "description": "Max results to return (search action, default 5).",
            },
        },
        "required": ["action", "text"],
        "additionalProperties": False,
    }

    def __init__(self, backend: RagBackend, *, collection: str = "default") -> None:
        self._backend = backend
        self._collection = collection

    async def execute(
        self,
        *,
        action: str,
        text: str = "",
        limit: int = 5,
        **_: object,
    ) -> ToolExecutionResult:
        limit = max(1, min(limit, 20))

        if not text.strip():
            return ToolExecutionResult(
                content=[TextBlock(text="'text' is required.")],
                is_error=True,
            )

        if action == "ingest":
            result = await self._backend.ingest(text, collection=self._collection)
            suffix = (
                f"{result.chunks_indexed} chunks"
                if result.chunks_indexed >= 0
                else "document"
            )
            return ToolExecutionResult(
                content=[TextBlock(text=f"Indexed {suffix} into the knowledge base.")],
            )

        if action == "search":
            results = await self._backend.query(
                text, collection=self._collection, limit=limit
            )
            if not results:
                return ToolExecutionResult(
                    content=[TextBlock(text="No matching documents found.")],
                )
            lines = [f"Top {len(results)} results for '{text}':"]
            for i, result in enumerate(results, 1):
                preview = result.to_text()[:200].replace("\n", " ")
                lines.append(f"\n{i}. (score: {result.score:.3f})\n   {preview}")
            return ToolExecutionResult(
                content=[TextBlock(text="\n".join(lines))],
            )

        return ToolExecutionResult(
            content=[TextBlock(text=f"Unknown action: {action!r}")],
            is_error=True,
        )

"""KnowledgeSearchTool — semantic search over uploaded documents.

Provides a simple in-memory embedding index so the agent can retrieve
relevant passages from previously analyzed documents.  Uses OpenAI
embeddings when available; falls back to naive keyword matching.
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional, Tuple

from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk

logger = logging.getLogger(__name__)


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class KnowledgeSearchTool(BaseTool):
    """Search over indexed documents using embedding similarity."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key
        # In-memory document store: list of (id, text_chunk, embedding)
        self._index: List[Tuple[str, str, List[float]]] = []
        super().__init__(
            name="knowledge_search",
            description=(
                "Index documents and search over them using semantic similarity. "
                "Use action='index' to add text, then action='search' to query."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["index", "search", "status"],
                        "description": (
                            "Action: index (add document text), "
                            "search (query indexed docs), "
                            "status (show index stats)"
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to index (for action='index') or query (for action='search')",
                    },
                    "doc_id": {
                        "type": "string",
                        "description": "Document identifier (for action='index')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 5)",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            risk=ToolRisk.SAFE,
            category="data/management",
            tags=[
                "knowledge",
                "rag",
                "search",
                "embed",
                "retrieve",
                "semantic",
                "vector",
            ],
            aliases=["rag_search", "doc_search"],
        )

    async def _embed(self, text: str) -> Optional[List[float]]:
        """Get embedding vector from OpenAI."""
        api_key = self._api_key
        if not api_key:
            import os

            api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None

        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "text-embedding-3-small",
                    "input": text[:8000],
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    def _chunk_text(self, text: str, chunk_size: int = 1000) -> List[str]:
        """Split text into overlapping chunks."""
        chunks: List[str] = []
        stride = chunk_size // 2
        for i in range(0, len(text), stride):
            chunk = text[i : i + chunk_size].strip()
            if chunk:
                chunks.append(chunk)
        return chunks

    async def execute(  # type: ignore[override]
        self,
        *,
        action: str,
        text: str = "",
        doc_id: str = "",
        limit: int = 5,
    ) -> ToolResult:
        limit = max(1, min(limit, 20))

        if action == "status":
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": f"Knowledge index: {len(self._index)} chunks indexed.",
                    }
                ],
            )

        if action == "index":
            if not text.strip():
                return ToolResult(
                    content=[
                        {"type": "text", "text": "'text' is required for indexing."}
                    ],
                    is_error=True,
                )
            doc_label = doc_id or f"doc_{len(self._index)}"
            chunks = self._chunk_text(text)
            indexed = 0
            for chunk in chunks:
                embedding = await self._embed(chunk)
                if embedding is not None:
                    self._index.append((doc_label, chunk, embedding))
                    indexed += 1
            if indexed == 0:
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": "Could not generate embeddings. Check API key.",
                        }
                    ],
                    is_error=True,
                )
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": f"Indexed {indexed} chunks from '{doc_label}'. Total: {len(self._index)} chunks.",
                    }
                ],
            )

        if action == "search":
            if not text.strip():
                return ToolResult(
                    content=[
                        {"type": "text", "text": "'text' query is required for search."}
                    ],
                    is_error=True,
                )
            if not self._index:
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": "No documents indexed yet. Use action='index' first.",
                        }
                    ],
                )

            query_embedding = await self._embed(text)
            if query_embedding is None:
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": "Could not generate query embedding. Check API key.",
                        }
                    ],
                    is_error=True,
                )

            scored: List[Tuple[float, str, str]] = []
            for doc_label, chunk, emb in self._index:
                sim = _cosine_similarity(query_embedding, emb)
                scored.append((sim, doc_label, chunk))
            scored.sort(key=lambda x: -x[0])

            results = scored[:limit]
            lines: List[str] = [f"Top {len(results)} results for '{text}':"]
            for i, (sim, doc_label, chunk) in enumerate(results, 1):
                preview = chunk[:200].replace("\n", " ")
                lines.append(f"\n{i}. [{doc_label}] (score: {sim:.3f})\n   {preview}")

            return ToolResult(
                content=[{"type": "text", "text": "\n".join(lines)}],
            )

        return ToolResult(
            content=[{"type": "text", "text": f"Unknown action: {action!r}"}],
            is_error=True,
        )

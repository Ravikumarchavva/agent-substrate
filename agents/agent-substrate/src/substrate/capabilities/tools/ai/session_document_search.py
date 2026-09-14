"""SessionDocumentSearchTool — search documents uploaded in the current chat
session, across the user's own per-user Lance index (vector/tree/graph —
see ``capabilities/knowledge/session_ingest.py`` for the write side).

Sibling to ``KnowledgeSearchTool``, not a replacement: that tool addresses
the tenant's *standing* knowledge base (``tenants/<tid>/knowledge/<kb_id>/``,
shared across every user); this one addresses documents a specific user
uploaded into chat — session-ephemeral in origin, but scoped per-user (not
per-session) in storage precisely so a query can span the user's own recent
sessions, not just the active one — see the storage plan's "why per-user,
not per-session" reasoning.

Scoping reads the same ContextVars ``TaskManagerTool``/``KnowledgeSearchTool``
already use (``agents/storage/tasks.py`` — stamped inside
``ReActAgent._handle_message()``, not serving code, per this project's own
documented Worker-isolation gotcha), so no new request-plumbing is needed.
"""

from __future__ import annotations

from substrate.agents.storage.tasks import (
    current_tenant_id,
    current_thread_id,
    current_user_id,
)
from substrate.kernel import TextBlock
from substrate.kernel.llm import EmbeddingClient, LLMClient
from substrate.kernel.storage.vector import SearchResult
from substrate.kernel.tools import ToolExecutionResult, ToolType
from substrate.logger import setup_logging

logger = setup_logging()

_VECTOR_COLLECTION = "vectors"
_PAGEINDEX_COLLECTION = "documents"


class SessionDocumentSearchTool:
    """Search the active user's own uploaded chat documents."""

    tool_type = ToolType.KNOWLEDGE
    name: str = "session_document_search"
    description: str = (
        "Search documents the user has uploaded in chat (this session or "
        "recent ones) — NOT the project's standing knowledge base (use "
        "knowledge_search for that). "
        "mode='vector' (default): hybrid semantic + keyword search over "
        "chunks — the general-purpose choice. "
        "mode='tree': navigate a document's outline/table-of-contents to a "
        "specific section — use when the user names a document by title "
        "and asks about a specific part of it. "
        "mode='graph': answer relationship questions across uploaded "
        "documents (e.g. comparing entities mentioned in different files) "
        "via vector search enriched with an extracted entity graph. "
        "current_session_only=true restricts to files uploaded in this "
        "exact conversation; false (default) also searches the user's "
        "other recent sessions."
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "mode": {
                "type": "string",
                "enum": ["vector", "tree", "graph"],
                "description": "Search strategy — see tool description. Default: vector.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 5, max 20). Ignored by mode='tree'.",
            },
            "current_session_only": {
                "type": "boolean",
                "description": "Restrict to this conversation's own uploads (default false).",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        cfg,
        embedding_client: EmbeddingClient,
        model_client: LLMClient,
    ) -> None:
        self._cfg = cfg
        self._embedding_client = embedding_client
        self._model_client = model_client

    def _scope(self) -> tuple[str, str, str] | None:
        """(tenant_id, user_id, session_id) from the active chat context, or
        None if called outside one (e.g. a test harness with no agent run
        in flight) — the tool has nothing to scope to in that case."""
        tenant_id = current_tenant_id.get()
        user_id = current_user_id.get()
        session_id = current_thread_id.get() or ""
        if not tenant_id or not user_id:
            return None
        return tenant_id, user_id, session_id

    async def execute(
        self,
        *,
        query: str,
        mode: str = "vector",
        limit: int | None = None,
        current_session_only: bool = False,
        **_: object,
    ) -> ToolExecutionResult:
        if not query.strip():
            return ToolExecutionResult(
                content=[TextBlock(text="'query' is required.")], is_error=True
            )
        scope = self._scope()
        if scope is None:
            return ToolExecutionResult(
                content=[TextBlock(text="No active session to search.")],
                is_error=True,
            )
        tenant_id, user_id, session_id = scope
        # RAG_FINAL_K — same default this tool's sibling KnowledgeSearchTool
        # uses, sourced from the same config field.
        default_limit = getattr(self._cfg, "RAG_FINAL_K", 5)
        limit = max(1, min(limit if limit is not None else default_limit, 20))
        session_filter = (
            {"session_id": session_id} if current_session_only and session_id else None
        )

        if mode == "tree":
            results = await self._search_tree(tenant_id, user_id, query, limit=limit)
        elif mode == "graph":
            results = await self._search_graph(
                tenant_id, user_id, query, limit=limit, filter=session_filter
            )
        else:
            results = await self._search_vector(
                tenant_id, user_id, query, limit=limit, filter=session_filter
            )

        if not results:
            return ToolExecutionResult(content=[TextBlock(text="No matching documents found.")])

        lines = [f"Top {len(results)} results for {query!r} (mode={mode}):"]
        for result in results:
            filename = result.metadata.get("filename", "unknown")
            lines.append(f"\n[{filename}] (score: {result.score:.3f})\n{result.to_text()[:4000]}")
        return ToolExecutionResult(content=[TextBlock(text="\n".join(lines))])

    async def _search_vector(
        self, tenant_id: str, user_id: str, query: str, *, limit: int, filter: dict | None
    ) -> list[SearchResult]:
        from substrate.infrastructure.serving_factory import (
            build_session_index_vector_store,
        )

        store = build_session_index_vector_store(self._cfg, tenant_id, user_id)
        embedded = await self._embedding_client.embed([query])
        return await store.hybrid_search(
            embedded.embeddings[0],
            query,
            collection=_VECTOR_COLLECTION,
            limit=limit,
            filter=filter,
        )

    async def _search_tree(
        self, tenant_id: str, user_id: str, query: str, *, limit: int
    ) -> list[SearchResult]:
        from substrate.capabilities.knowledge.page_pipeline import PageIndexRAGPipeline
        from substrate.infrastructure.serving_factory import build_page_index_memory

        memory = build_page_index_memory(self._cfg, tenant_id, user_id)
        pipeline = PageIndexRAGPipeline(model_client=self._model_client, memory_store=memory)
        return await pipeline.query(query, collection=_PAGEINDEX_COLLECTION, limit=limit)

    async def _search_graph(
        self, tenant_id: str, user_id: str, query: str, *, limit: int, filter: dict | None
    ) -> list[SearchResult]:
        from substrate.capabilities.knowledge.graph_rag import GraphRAGPipeline
        from substrate.capabilities.knowledge.pipeline import RAGPipeline
        from substrate.infrastructure.serving_factory import (
            build_session_graph_store,
            build_session_index_vector_store,
        )

        vector_store = build_session_index_vector_store(self._cfg, tenant_id, user_id)
        rag = RAGPipeline(embedding_client=self._embedding_client, vector_store=vector_store)
        # No session_id passed to the graph-store factory here: reads span
        # every entity/relationship this user has ever had extracted,
        # regardless of current_session_only (that flag only filters the
        # vector half via `filter`) — narrowing graph reads by session isn't
        # supported by GraphRAGPipeline.query's own keyword-match path today.
        graph_store = build_session_graph_store(self._cfg, tenant_id, user_id)
        pipeline = GraphRAGPipeline(
            rag_pipeline=rag, graph_store=graph_store, model_client=self._model_client
        )
        return await pipeline.query(query, collection=_VECTOR_COLLECTION, limit=limit)


__all__ = ["SessionDocumentSearchTool"]

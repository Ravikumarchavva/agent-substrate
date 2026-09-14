"""Session-document ingestion — the write side of the per-user index bundle
(vector + PageIndex tree + knowledge graph, see the storage plan).

Replaces the old per-thread Postgres staging-collection flow
(``routes/files.py::_stage_uploaded_doc`` used to call
``rag_backend.ingest(collection=f"staging:{file_id}")``, later promoted into
``collection=str(thread_id)`` at send time — see
``routes/chat_context.py``). That two-phase dance existed because moving
data between Postgres collections was itself the "commit" step; it isn't
needed here — every table already carries a ``session_id`` column, so a
document is written once, already correctly scoped, and there's nothing to
move later. The old ``FileMetadata.staged_at``/``rag_ingested_at``/
``staging_error`` columns are still used, just for what they always meant:
"has this file finished processing" and "has the daily quota been charged
for it" — see the call sites in ``routes/files.py``/``routes/chat_context.py``.

Extraction goes through the shared ``extract_document`` (document-
intelligence-service-or-local-fallback) rather than being reimplemented here
— same quality, no drift between the tenant-KB flow and this one. Unlike the
old approach of reaching into ``LocalRagBackend._load``, ``extract_document``
doesn't care which ``RagBackend`` implementation the caller uses for
storage, so this function works regardless of ``RAG_BACKEND``.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from typing import Any

from substrate.kernel.llm import EmbeddingClient, LLMClient


@dataclass
class SessionIngestResult:
    document_id: str
    chunks: int
    pageindex_nodes: int
    graph_extraction_attempted: bool


async def ingest_session_document(
    *,
    data: bytes,
    filename: str,
    content_type: str,
    tenant_id: str,
    user_id: str,
    session_id: str,
    cfg: Any,
    embedding_client: EmbeddingClient,
    model_client: LLMClient,
    rag_backend: Any,
) -> SessionIngestResult:
    """Extract, chunk, embed, and index one uploaded document into the
    caller's per-user Lance-backed vector/tree/graph stores.
    """
    from substrate.capabilities.knowledge.graph_rag import GraphRAGPipeline
    from substrate.capabilities.knowledge.page_pipeline import PageIndexRAGPipeline
    from substrate.capabilities.knowledge.pipeline import RAGPipeline
    from substrate.infrastructure.serving_factory import (
        build_page_index_memory,
        build_session_graph_store,
        build_session_index_vector_store,
    )
    from substrate.integrations.llm.endpoint import InferenceEndpoint
    from substrate.kernel.storage.vector import Document
    from substrate.runtimes.document_intelligence.extract import extract_document

    document_id = uuid.uuid4().hex
    endpoint = None
    if cfg.DOCUMENT_INTELLIGENCE_SERVICE_URL:
        endpoint = InferenceEndpoint(
            model="",
            base_url=cfg.DOCUMENT_INTELLIGENCE_SERVICE_URL,
            api_key=cfg.DOCUMENT_INTELLIGENCE_AUTH_TOKEN,
            timeout_s=cfg.DOCUMENT_INTELLIGENCE_TIMEOUT_S,
        )
    result = await extract_document(data, filename, content_type, endpoint=endpoint)
    text_documents = [
        Document.from_text(
            page.text,
            metadata={
                "filename": filename,
                "content_type": content_type,
                "engine": result.engine,
                "page_number": page.page_number,
                "total_pages": len(result.pages),
            },
        )
        for page in result.pages
        if page.text.strip()
    ]
    if not text_documents:
        return SessionIngestResult(
            document_id=document_id,
            chunks=0,
            pageindex_nodes=0,
            graph_extraction_attempted=False,
        )

    # Tag every chunk with session_id/document_id *before* embedding — this
    # is the whole mechanism that replaces the old staging/promote dance:
    # rows are already correctly scoped for SessionDocumentSearchTool's
    # `filter={"session_id": ...}` queries the moment they're written.
    # Document is frozen — dataclasses.replace(), not attribute assignment.
    text_documents = [
        dataclasses.replace(
            doc,
            metadata={
                **doc.metadata,
                "session_id": session_id,
                "document_id": document_id,
                "filename": filename,
            },
        )
        for doc in text_documents
    ]

    from substrate.capabilities.knowledge.chunking import recommend_chunk_params

    # None (the default) -> derive from the configured embedding model,
    # same "explicit wins, else model-informed default" resolution
    # backends/factory.py's build_rag_backend uses for the tenant-KB path
    # -- see recommend_chunk_params's own docstring for why.
    _recommended_size, _recommended_overlap = recommend_chunk_params(
        getattr(cfg, "EMBEDDING_MODEL", "") or ""
    )
    chunk_size = getattr(cfg, "RAG_CHUNK_SIZE", None)
    chunk_overlap = getattr(cfg, "RAG_CHUNK_OVERLAP", None)

    vector_store = build_session_index_vector_store(cfg, tenant_id, user_id)
    rag = RAGPipeline(
        embedding_client=embedding_client,
        vector_store=vector_store,
        default_chunk_size=chunk_size if chunk_size is not None else _recommended_size,
        default_chunk_overlap=(
            chunk_overlap if chunk_overlap is not None else _recommended_overlap
        ),
    )
    chunks = await rag.ingest_documents(text_documents, collection="vectors")

    memory_store = build_page_index_memory(cfg, tenant_id, user_id)
    page_pipeline = PageIndexRAGPipeline(model_client=model_client, memory_store=memory_store)
    page_texts = [doc.to_text() for doc in text_documents]
    pageindex_nodes = await page_pipeline.ingest(
        page_texts, collection="documents", title=filename, strategy="flat"
    )

    graph_store = build_session_graph_store(
        cfg, tenant_id, user_id, session_id=session_id
    )
    # rag_pipeline arg is required by GraphRAGPipeline's constructor but
    # unused here — we call _extract_and_store_graph directly (see module
    # docstring) instead of ingest_with_graph, which would re-chunk raw
    # text through a different, simpler path than extract_document's
    # already-extracted per-page Documents above.
    graph_pipeline = GraphRAGPipeline(
        rag_pipeline=rag, graph_store=graph_store, model_client=model_client
    )
    # _extract_and_store_graph already catches and logs its own failures
    # internally (LLM call + JSON parse + store, all one try/except) rather
    # than propagating — so "attempted" is all this can honestly report,
    # not "succeeded".
    full_text = "\n\n".join(page_texts)
    await graph_pipeline._extract_and_store_graph(full_text)

    return SessionIngestResult(
        document_id=document_id,
        chunks=chunks,
        pageindex_nodes=pageindex_nodes,
        graph_extraction_attempted=True,
    )


__all__ = ["SessionIngestResult", "ingest_session_document"]

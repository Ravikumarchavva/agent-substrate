"""RAG API routes — ingest, query, and collection management.

Endpoints:
    POST /rag/ingest      — Ingest text into a collection
    POST /rag/query       — Query a collection for relevant documents
    GET  /rag/collections — List all collections
    DELETE /rag/collections/{name} — Delete a collection
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ravi.serving.monolith.security.deps import get_current_user

router = APIRouter(
    prefix="/rag",
    tags=["rag"],
    dependencies=[Depends(get_current_user)],
)


# ── Request / Response schemas ────────────────────────────────────────────────


class IngestRequest(BaseModel):
    content: str | list[str]
    collection: str = "default"
    chunker: str = "text"
    metadata: Optional[dict[str, Any]] = None
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None


class IngestResponse(BaseModel):
    chunks_stored: int
    collection: str


class QueryRequest(BaseModel):
    question: str
    collection: str = "default"
    limit: int = Field(default=5, ge=1, le=100)
    filter: Optional[dict[str, Any]] = None
    generate_answer: bool = False
    system_prompt: Optional[str] = None


class QueryResult(BaseModel):
    id: str
    text: str
    score: float
    metadata: dict[str, Any] = {}


class QueryResponse(BaseModel):
    results: list[QueryResult]
    answer: Optional[str] = None


class CollectionListResponse(BaseModel):
    collections: list[str]


class DeleteCollectionResponse(BaseModel):
    deleted: int
    collection: str


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_pipeline(request: Request):
    """Get the RAG pipeline from app state."""
    pipeline = getattr(request.app.state, "rag_pipeline", None)
    if pipeline is None:
        raise HTTPException(
            status_code=503,
            detail="RAG pipeline not configured. Set EMBEDDING_MODEL and ensure pgvector is available.",
        )
    return pipeline


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest, request: Request) -> IngestResponse:
    """Ingest text content into the vector store."""
    pipeline = _get_pipeline(request)

    chunks = await pipeline.ingest(
        body.content,
        collection=body.collection,
        chunker=body.chunker,
        metadata=body.metadata,
        chunk_size=body.chunk_size,
        chunk_overlap=body.chunk_overlap,
    )

    return IngestResponse(chunks_stored=chunks, collection=body.collection)


@router.post("/query", response_model=QueryResponse)
async def query(body: QueryRequest, request: Request) -> QueryResponse:
    """Query the vector store for relevant documents."""
    pipeline = _get_pipeline(request)

    results = await pipeline.query(
        body.question,
        collection=body.collection,
        limit=body.limit,
        filter=body.filter,
    )

    query_results = [
        QueryResult(
            id=r.id,
            text=r.to_text(),
            score=r.score,
            metadata=r.metadata,
        )
        for r in results
    ]

    answer = None
    if body.generate_answer:
        model_client = getattr(request.app.state, "model_client", None)
        if model_client:
            answer = await pipeline.query_with_context(
                body.question,
                collection=body.collection,
                model_client=model_client,
                limit=body.limit,
                system=body.system_prompt,
                filter=body.filter,
            )

    return QueryResponse(results=query_results, answer=answer)


@router.get("/collections", response_model=CollectionListResponse)
async def list_collections(request: Request) -> CollectionListResponse:
    """List all vector store collections."""
    pipeline = _get_pipeline(request)
    collections = await pipeline._store.list_collections()
    return CollectionListResponse(collections=collections)


@router.delete("/collections/{name}", response_model=DeleteCollectionResponse)
async def delete_collection(name: str, request: Request) -> DeleteCollectionResponse:
    """Delete all documents in a collection."""
    pipeline = _get_pipeline(request)
    deleted = await pipeline._store.delete_collection(name)
    return DeleteCollectionResponse(deleted=deleted, collection=name)

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

from substrate.serving.monolith.security.deps import get_current_user

router = APIRouter(
    prefix="/rag",
    tags=["rag"],
    dependencies=[Depends(get_current_user)],
)


# ── Request / Response schemas ────────────────────────────────────────────────


class IngestRequest(BaseModel):
    content: str
    collection: str = "default"
    filename: str = "upload.txt"
    metadata: Optional[dict[str, Any]] = None


class IngestResponse(BaseModel):
    chunks_stored: int
    collection: str
    document_id: Optional[str] = None


class QueryRequest(BaseModel):
    question: str
    collection: str = "default"
    limit: int = Field(default=5, ge=1, le=100)
    generate_answer: bool = False


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


def _get_backend(request: Request):
    """Get the RagBackend from app state."""
    backend = getattr(request.app.state, "rag_backend", None)
    if backend is None:
        raise HTTPException(
            status_code=503,
            detail="RAG backend not configured. Set EMBEDDING_MODEL and ensure pgvector is available.",
        )
    return backend


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest, request: Request) -> IngestResponse:
    """Ingest text content into the knowledge base."""
    backend = _get_backend(request)

    metadata = dict(body.metadata or {})
    metadata.setdefault("filename", body.filename)

    result = await backend.ingest(
        body.content.encode("utf-8"),
        collection=body.collection,
        metadata=metadata,
    )

    return IngestResponse(
        chunks_stored=result.chunks_indexed,
        collection=body.collection,
        document_id=result.document_id,
    )


@router.post("/query", response_model=QueryResponse)
async def query(body: QueryRequest, request: Request) -> QueryResponse:
    """Query the knowledge base for relevant documents."""
    backend = _get_backend(request)

    results = await backend.query(
        body.question,
        collection=body.collection,
        limit=body.limit,
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
        answer = await backend.query_with_context(
            body.question,
            collection=body.collection,
            limit=body.limit,
        )

    return QueryResponse(results=query_results, answer=answer)


@router.get("/collections", response_model=CollectionListResponse)
async def list_collections(request: Request) -> CollectionListResponse:
    """List all vector store collections. Local backend only."""
    backend = _get_backend(request)
    if backend.name != "local":
        raise HTTPException(
            status_code=400,
            detail=f"Collection listing isn't supported by the {backend.name!r} backend.",
        )
    collections = await backend.list_collections()
    return CollectionListResponse(collections=collections)


@router.delete("/collections/{name}", response_model=DeleteCollectionResponse)
async def delete_collection(name: str, request: Request) -> DeleteCollectionResponse:
    """Delete all documents in a collection. Local backend only."""
    backend = _get_backend(request)
    if backend.name != "local":
        raise HTTPException(
            status_code=400,
            detail=f"Collection deletion isn't supported by the {backend.name!r} backend.",
        )
    deleted = await backend.delete_collection(name)
    return DeleteCollectionResponse(deleted=deleted, collection=name)

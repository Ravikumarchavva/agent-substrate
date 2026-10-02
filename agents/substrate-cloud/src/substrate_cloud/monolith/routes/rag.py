"""Knowledge-base API — add documents, search them, list and remove knowledge bases.

Endpoints:
    POST   /rag/ingest                 — add a document (text) to a knowledge base
    POST   /rag/query                  — search a knowledge base
    GET    /rag/collections            — the caller's tenant's knowledge bases
    DELETE /rag/collections/{name}     — remove one

A knowledge base is named by the caller, but **which tenant's** it is comes from the authenticated claims alone: the collection is always
``knowledge_collection(claims.tenant_id, name)``, so no request can read, list or delete another tenant's.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from substrate.documents import DocumentError
from substrate.workspace.layout import tenant_prefix
from substrate_cloud.documents_library import knowledge_collection_for
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.shared.auth.claims import AuthClaims

router = APIRouter(prefix="/rag", tags=["rag"])


# ── Request / Response schemas ────────────────────────────────────────────────


class IngestRequest(BaseModel):
    content: str
    knowledge_base: str = "default"
    filename: str = "upload.txt"
    metadata: Optional[dict[str, Any]] = None


class IngestResponse(BaseModel):
    knowledge_base: str
    document_id: str
    sections: int
    pages: int
    duplicate: bool = False
    warnings: list[str] = []


class QueryRequest(BaseModel):
    question: str
    knowledge_base: str = "default"
    document: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=20)


class QueryResult(BaseModel):
    id: str
    text: str
    score: float
    metadata: dict[str, Any] = {}


class QueryResponse(BaseModel):
    results: list[QueryResult]
    note: Optional[str] = None


class KnowledgeBase(BaseModel):
    name: str
    documents: int


class CollectionListResponse(BaseModel):
    collections: list[KnowledgeBase]


class DeleteCollectionResponse(BaseModel):
    deleted: int
    knowledge_base: str


# ── Helpers ───────────────────────────────────────────────────────────────────


def _knowledge(request: Request):
    knowledge = getattr(request.app.state, "knowledge", None)
    if knowledge is None:
        raise HTTPException(status_code=503, detail="Knowledge bases are not configured: no store or object storage.")
    return knowledge


def _collection(claims: AuthClaims, name: str) -> str:
    collection = knowledge_collection_for(claims.tenant_id, name)
    if collection is None:
        raise HTTPException(status_code=422, detail="Invalid knowledge base name.")
    return collection


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest, request: Request, claims: AuthClaims = Depends(get_current_user)) -> IngestResponse:
    """Add text to a knowledge base."""
    knowledge = _knowledge(request)
    metadata = dict(body.metadata or {})
    metadata.setdefault("filename", body.filename)
    try:
        added = await knowledge.add(body.content.encode("utf-8"), body.filename, collection=_collection(claims, body.knowledge_base), metadata=metadata)
    except DocumentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return IngestResponse(
        knowledge_base=body.knowledge_base,
        document_id=added.document,
        sections=added.sections,
        pages=added.pages,
        duplicate=added.duplicate,
        warnings=list(added.warnings),
    )


@router.post("/query", response_model=QueryResponse)
async def query(body: QueryRequest, request: Request, claims: AuthClaims = Depends(get_current_user)) -> QueryResponse:
    """Search a knowledge base: the sections that answer the question, best first."""
    knowledge = _knowledge(request)
    hits = await knowledge.find(collection=_collection(claims, body.knowledge_base), query=body.question, document=body.document, limit=body.limit)
    return QueryResponse(
        results=[
            QueryResult(
                id=f"{hit.document.document}:{hit.section.position}",
                text=hit.snippet,
                score=hit.score,
                metadata={
                    "document": hit.document.document,
                    "filename": hit.document.filename,
                    "title": hit.document.title,
                    "section": hit.section.position,
                    "section_title": hit.section.title,
                    "pages": [hit.section.first_page, hit.section.last_page],
                    **({"image": hit.image} if hit.image else {}),
                    **hit.document.meta,
                },
            )
            for hit in hits
        ],
        note=getattr(hits, "note", None),
    )


@router.get("/collections", response_model=CollectionListResponse)
async def list_collections(request: Request, claims: AuthClaims = Depends(get_current_user)) -> CollectionListResponse:
    """The caller's tenant's knowledge bases (and only theirs)."""
    knowledge = _knowledge(request)
    root = f"{tenant_prefix(claims.tenant_id)}/knowledge/"
    found = await knowledge.collections(under=root)
    return CollectionListResponse(
        collections=[KnowledgeBase(name=collection[len(root) :].removesuffix("/library"), documents=count) for collection, count in found if collection.endswith("/library")]
    )


@router.delete("/collections/{name}", response_model=DeleteCollectionResponse)
async def delete_collection(name: str, request: Request, claims: AuthClaims = Depends(get_current_user)) -> DeleteCollectionResponse:
    """Remove a knowledge base — its documents' bundles, catalog and vectors. Only the caller's tenant's."""
    knowledge = _knowledge(request)
    deleted = await knowledge.erase_under(_collection(claims, name))
    return DeleteCollectionResponse(deleted=deleted, knowledge_base=name)


__all__ = ["router"]

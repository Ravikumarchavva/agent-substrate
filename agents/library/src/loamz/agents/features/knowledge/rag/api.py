from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.knowledge.rag.service import RagService, rag

Service = Annotated[RagService, Depends(use(rag))]

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


class IngestRequest(BaseModel):
    content: str
    knowledge_base: str = "default"
    filename: str = "upload.txt"
    metadata: dict[str, Any] | None = None


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
    document: str | None = None
    limit: int = Field(default=5, ge=1, le=20)


class QueryResult(BaseModel):
    id: str
    text: str
    score: float
    metadata: dict[str, Any] = {}


class QueryResponse(BaseModel):
    results: list[QueryResult]
    note: str | None = None


class KnowledgeBase(BaseModel):
    name: str
    documents: int


class CollectionList(BaseModel):
    items: list[KnowledgeBase]


class DeleteCollectionResponse(BaseModel):
    deleted: int
    knowledge_base: str


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest, caller: CallerDep, service: Service) -> dict[str, Any]:
    return await service.ingest(
        caller,
        content=body.content,
        knowledge_base=body.knowledge_base,
        filename=body.filename,
        metadata=body.metadata,
    )


@router.post("/query", response_model=QueryResponse)
async def query(body: QueryRequest, caller: CallerDep, service: Service) -> dict[str, Any]:
    return await service.query(
        caller,
        question=body.question,
        knowledge_base=body.knowledge_base,
        document=body.document,
        limit=body.limit,
    )


@router.get("/collections", response_model=CollectionList)
async def list_collections(caller: CallerDep, service: Service) -> dict[str, Any]:
    return {"items": await service.collections(caller)}


@router.delete("/collections/{name}", response_model=DeleteCollectionResponse)
async def delete_collection(name: str, caller: CallerDep, service: Service) -> dict[str, Any]:
    return {"deleted": await service.delete_collection(caller, name), "knowledge_base": name}

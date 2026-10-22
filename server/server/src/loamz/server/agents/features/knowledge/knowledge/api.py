from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, UploadFile

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.knowledge.knowledge.service import KnowledgeService, knowledge

Service = Annotated[KnowledgeService, Depends(use(knowledge))]

router = APIRouter(prefix="/api/v1/internal/knowledge", tags=["knowledge"])


@router.post("/{knowledge_base_id}/documents")
async def upload_document(
    knowledge_base_id: str,
    caller: CallerDep,
    service: Service,
    file: UploadFile = File(...),  # noqa: B008
) -> dict[str, Any]:
    return await service.upload_document(
        caller,
        knowledge_base_id,
        data=await file.read(),
        filename=file.filename,
        content_type=file.content_type,
    )

"""Tenant-scoped knowledge-base document storage."""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from substrate.capabilities.storage.layout import knowledge_document_prefix
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims

router = APIRouter(prefix="/internal/knowledge", tags=["knowledge"])


@router.post("/{knowledge_base_id}/documents")
async def upload_document(
    knowledge_base_id: str,
    file: UploadFile = File(...),
    claims: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
) -> dict:
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File storage is not configured")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="Document is empty")
    document_id = str(uuid.uuid4())
    name = (file.filename or "document").replace("/", "_").replace("\\", "_")
    prefix = knowledge_document_prefix(claims.tenant_id, knowledge_base_id, document_id)
    storage_key = f"{prefix}/original/{name}"
    await ctx.file_store.upload(
        storage_key, data, content_type=file.content_type or "application/octet-stream"
    )
    return {
        "document_id": document_id,
        "storage_key": storage_key,
        "checksum_sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }

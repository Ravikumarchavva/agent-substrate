"""Tenant-scoped knowledge-base document storage: the original is kept, and the document is filed in the knowledge base."""

from __future__ import annotations

import hashlib
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from substrate.documents import DocumentError
from substrate.workspace.layout import knowledge_document_prefix
from substrate_cloud.documents_library import knowledge_collection_for
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.shared.auth.claims import AuthClaims

logger = logging.getLogger(__name__)

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
    collection = knowledge_collection_for(claims.tenant_id, knowledge_base_id)
    if collection is None:
        raise HTTPException(status_code=422, detail="Invalid knowledge base id")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="Document is empty")
    document_id = str(uuid.uuid4())
    name = (file.filename or "document").replace("/", "_").replace("\\", "_")
    prefix = knowledge_document_prefix(claims.tenant_id, knowledge_base_id, document_id)
    storage_key = f"{prefix}/original/{name}"
    content_type = file.content_type or "application/octet-stream"
    await ctx.files_for(claims.tenant_id).upload(storage_key, data, content_type=content_type)
    checksum = hashlib.sha256(data).hexdigest()

    # File it in the knowledge base (read once; chunked and embedded if an embedder is configured). The original is kept either way: a
    # document that cannot be read is reported, not lost, so the caller can retry or fix it.
    indexed: dict = {"indexed": False}
    if ctx.knowledge is not None:
        try:
            added = await ctx.knowledge.add(
                data,
                name,
                collection=collection,
                resource=storage_key,
                content_type=content_type,
                sha256=checksum,
                metadata={"document_id": document_id, "knowledge_base_id": knowledge_base_id},
            )
            indexed = {"indexed": True, "library_document": added.document, "sections": added.sections, "pages": added.pages, "warnings": list(added.warnings)}
        except DocumentError as exc:
            indexed = {"indexed": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — the original is stored; a failure to index must not lose the upload
            logger.warning("indexing %s into knowledge base %s failed: %s", name, knowledge_base_id, exc)
            indexed = {"indexed": False, "error": "the document could not be indexed"}
    return {
        "document_id": document_id,
        "storage_key": storage_key,
        "checksum_sha256": checksum,
        "size_bytes": len(data),
        **indexed,
    }

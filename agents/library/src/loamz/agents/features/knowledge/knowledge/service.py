from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.knowledge.documents.service import DocumentsService, documents
from loamz.agents.features.knowledge.enrichment.service import EnrichmentService, enrichment
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.ports.files import FileStore
from loamz.agents.kernel.types.ids import new_id
from loamz.agents.kernel.types.scope import Scope
from loamz.agents.stores.scoped import fence_objects
from loamz.agents.stores.workspace.layout import knowledge_document_prefix
from loamz.data.storage.errors import InvalidRequestError, LoamzError

logger = logging.getLogger(__name__)


class KnowledgeService:
    """Uploading the originals of a knowledge base: the original is kept, then the document is filed (and later described)."""

    def __init__(self, files: FileStore, docs: DocumentsService, enrich: EnrichmentService) -> None:
        self._files = files
        self._docs = docs
        self._enrich = enrich

    async def upload_document(
        self,
        caller: Caller,
        knowledge_base_id: str,
        *,
        data: bytes,
        filename: str | None,
        content_type: str | None,
    ) -> dict[str, Any]:
        collection = self._docs.knowledge_collection_for(caller.tenant_id, knowledge_base_id)
        if collection is None:
            raise InvalidRequestError("Invalid knowledge base id")
        if not data:
            raise InvalidRequestError("Document is empty")
        document_id = new_id()
        name = re.sub(r"[/\\]", "_", filename or "document")
        prefix = knowledge_document_prefix(caller.tenant_id, knowledge_base_id, document_id)
        storage_key = f"{prefix}/original/{name}"
        content_type = content_type or "application/octet-stream"
        files = fence_objects(self._files, Scope(tenant_id=caller.tenant_id))
        await files.upload(storage_key, data, content_type=content_type)
        checksum = hashlib.sha256(data).hexdigest()
        # The original is kept either way: a document that cannot be read is reported, not lost, so the caller can retry.
        indexed: dict[str, Any] = {"indexed": False}
        try:
            added = await self._docs.library.add(
                data,
                name,
                collection=collection,
                resource=storage_key,
                content_type=content_type,
                sha256=checksum,
                metadata={"document_id": document_id, "knowledge_base_id": knowledge_base_id},
            )
            self._enrich.submit(collection, added.document)
            indexed = {
                "indexed": True,
                "library_document": added.document,
                "sections": added.sections,
                "pages": added.pages,
                "warnings": list(added.warnings),
            }
        except LoamzError as exc:
            indexed = {"indexed": False, "error": str(exc)}
        except Exception:  # noqa: BLE001 - the original is stored; a failure to index must not lose the upload
            logger.warning(
                "indexing %s into knowledge base %s failed", name, knowledge_base_id, exc_info=True
            )
            indexed = {"indexed": False, "error": "the document could not be indexed"}
        return {
            "document_id": document_id,
            "storage_key": storage_key,
            "checksum_sha256": checksum,
            "size_bytes": len(data),
            **indexed,
        }

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Originals of a tenant's knowledge bases (a tenant erasure only; a person owns none)."""
        if user_id is not None:
            return 0
        return await fence_objects(self._files, Scope(tenant_id=tenant_id)).delete_prefix(
            f"tenants/{tenant_id}/knowledge/"
        )


def knowledge(container: Container) -> KnowledgeService:
    return KnowledgeService(
        container.file_store, container.get(documents), container.get(enrichment)
    )

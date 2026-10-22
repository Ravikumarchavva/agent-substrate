from __future__ import annotations

from typing import Any

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.agents.stores.workspace.layout import tenant_prefix
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.knowledge.documents.service import DocumentsService, documents
from loamz.server.agents.features.knowledge.enrichment.service import EnrichmentService, enrichment


class RagService:
    """Knowledge bases by name: which tenant's they are comes from the caller alone."""

    def __init__(self, docs: DocumentsService, enrich: EnrichmentService) -> None:
        self._docs = docs
        self._enrich = enrich

    async def ingest(
        self,
        caller: Caller,
        *,
        content: str,
        knowledge_base: str,
        filename: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        collection = self._docs.knowledge_collection(caller.tenant_id, knowledge_base)
        meta = dict(metadata or {})
        meta.setdefault("filename", filename)
        added = await self._docs.library.add(
            content.encode("utf-8"), filename, collection=collection, metadata=meta
        )
        if not added.duplicate:
            self._enrich.submit(collection, added.document)
        return {
            "knowledge_base": knowledge_base,
            "document_id": added.document,
            "sections": added.sections,
            "pages": added.pages,
            "duplicate": added.duplicate,
            "warnings": list(added.warnings),
        }

    async def query(
        self,
        caller: Caller,
        *,
        question: str,
        knowledge_base: str,
        document: str | None,
        limit: int,
    ) -> dict[str, Any]:
        collection = self._docs.knowledge_collection(caller.tenant_id, knowledge_base)
        hits = await self._docs.library.find(
            collection=collection, query=question, document=document, limit=limit
        )
        return {
            "results": [
                {
                    "id": f"{hit.document.document}:{hit.section.position}",
                    "text": hit.snippet,
                    "score": hit.score,
                    "metadata": {
                        "document": hit.document.document,
                        "filename": hit.document.filename,
                        "title": hit.document.title,
                        "section": hit.section.position,
                        "section_title": hit.section.title,
                        "pages": [hit.section.first_page, hit.section.last_page],
                        **hit.document.meta,
                    },
                }
                for hit in hits
            ],
            "note": hits.note,
        }

    async def collections(self, caller: Caller) -> list[dict[str, Any]]:
        root = f"{tenant_prefix(caller.tenant_id)}/knowledge/"
        found = await self._docs.library.collections(under=root)
        return [
            {"name": collection[len(root) :].removesuffix("/library"), "documents": count}
            for collection, count in found
            if collection.endswith("/library")
        ]

    async def delete_collection(self, caller: Caller, name: str) -> int:
        return await self._docs.library.erase_under(
            self._docs.knowledge_collection(caller.tenant_id, name)
        )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Knowledge bases belong to the tenant, not a person: only a tenant erasure removes them (documents.erase)."""
        return 0


def rag(container: Container) -> RagService:
    return RagService(container.get(documents), container.get(enrichment))

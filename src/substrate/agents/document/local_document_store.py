"""LocalFilesystemDocumentStore — the L1 default ``DocumentStore``.

One directory per document, no server, survives restarts::

    <root>/documents/<document_id>/
      metadata.json   — the DocumentMetadata
      chunks.json     — its DocumentChunks, in order
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path

from substrate.agents.storage.fs import atomic_write_json, safe_name
from substrate.kernel.document import DocumentChunk, DocumentMetadata


class LocalFilesystemDocumentStore:
    def __init__(self, root: str | Path = "./data/db/documents") -> None:
        self._root = Path(root)

    def _dir(self, document_id: str) -> Path:
        return self._root / "documents" / safe_name(document_id)

    async def save_document(
        self, metadata: DocumentMetadata, chunks: Sequence[DocumentChunk]
    ) -> None:
        directory = self._dir(metadata.id)
        # Chunks first: a document is only listed once its metadata exists, so a
        # crash between the two writes never exposes a document without chunks.
        atomic_write_json(
            directory / "chunks.json", [c.model_dump(mode="json") for c in chunks]
        )
        atomic_write_json(directory / "metadata.json", metadata.model_dump(mode="json"))

    async def get_document(self, document_id: str) -> DocumentMetadata | None:
        path = self._dir(document_id) / "metadata.json"
        if not path.exists():
            return None
        return DocumentMetadata.model_validate(json.loads(path.read_text(encoding="utf-8")))

    async def get_chunks(self, document_id: str) -> Sequence[DocumentChunk]:
        path = self._dir(document_id) / "chunks.json"
        if not path.exists():
            return []
        return [
            DocumentChunk.model_validate(c)
            for c in json.loads(path.read_text(encoding="utf-8"))
        ]

    async def list_documents(
        self, *, limit: int = 100, offset: int = 0
    ) -> Sequence[DocumentMetadata]:
        """Oldest first."""
        files = sorted(
            (self._root / "documents").glob("*/metadata.json"),
            key=lambda p: (p.stat().st_mtime_ns, p.parent.name),
        )
        return [
            DocumentMetadata.model_validate(json.loads(p.read_text(encoding="utf-8")))
            for p in files[offset : offset + limit]
        ]

    async def delete_document(self, document_id: str) -> None:
        shutil.rmtree(self._dir(document_id), ignore_errors=True)


__all__ = ["LocalFilesystemDocumentStore"]
